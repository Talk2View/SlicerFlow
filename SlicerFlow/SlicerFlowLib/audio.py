"""The microphone for dictation: ``talk2view.audio.AudioCapture`` on ``sounddevice``.

``sounddevice`` (PortAudio) records 16 kHz mono int16 through its own callback thread; the
frames are gathered under a lock, and ``stop()`` hands them back as one WAV file. Recording
never runs on Slicer's main thread: the UI server calls ``start``/``stop``/``cancel`` on
its router thread, and PortAudio calls the callback on its own. ``available()``,
``make_capture()`` and ``bundle_declares_microphone()`` are the exception -- the gate is
read from ``Runtime._capture()`` on Slicer's main thread, at start, and
``bundle_declares_microphone()`` makes a Qt call (``slicer.app.slicerHome``) that needs it;
neither is called from the router thread without a plist path already in hand.

``available()`` is the gate the mic button depends on: the module imports, a default
input device exists, and — on macOS — the app bundle declares
``NSMicrophoneUsageDescription`` in its Info.plist. Without that key macOS can never show a
prompt for this app (``README.md``, "Microphone"; ``Testing/Python/test_audio_probe.py``
records what a raw open does here, without asserting which of "silent forever" or "the
documented crash" it is), so the official Slicer.app gets no mic until a build declares
it. A microphone the user has denied does not raise on macOS: it delivers silence, which
``stop()`` reports as ``nothing_heard``.

Imports no Slicer or Qt module at import time, so ``tests/`` run it without Slicer.
"""

import importlib
import os
import plistlib
import sys
import threading

from talk2view.audio import MAX_CLIP_BYTES, CaptureError, pcm16_to_wav, peak_pcm16

SAMPLE_RATE = 16000
CHANNELS = 1
#: Below this peak (int16 units) the whole clip is taken as silence: a denied or muted mic.
SILENCE_PEAK = 200
PLIST_KEY = "NSMicrophoneUsageDescription"


def _import_sounddevice():
    try:
        return importlib.import_module("sounddevice")
    except Exception:  # ImportError, or PortAudio's own OSError when its library is missing
        return None


def bundle_declares_microphone(plist_path=None):
    """macOS: does the app bundle's Info.plist carry ``NSMicrophoneUsageDescription``?
    ``plist_path`` None reads ``<Slicer.app>/Contents/Info.plist`` through ``slicer``."""
    if plist_path is None:
        import slicer

        home = slicer.app.slicerHome  # <Slicer.app>/Contents
        plist_path = os.path.join(home, "Info.plist")
        if not os.path.isfile(plist_path):
            plist_path = os.path.join(os.path.dirname(home), "Info.plist")
    try:
        with open(plist_path, "rb") as fh:
            return bool(plistlib.load(fh).get(PLIST_KEY))
    except (OSError, ValueError):
        return False


def _has_input_device(sd):
    try:
        sd.query_devices(kind="input")
    except Exception:
        return False
    return True


def platform_allows(platform=sys.platform, *, plist_declares=None):
    """Whether this app may record here at all, asked before ``sounddevice`` is needed:
    everywhere but macOS, and there only when the bundle declares the microphone."""
    if platform != "darwin":
        return True
    return bool(plist_declares if plist_declares is not None else bundle_declares_microphone())


def available(platform=sys.platform, *, sounddevice_module=None, plist_declares=None):
    """Whether the mic button may show: sounddevice imports, an input device exists, and on
    macOS the bundle declares the microphone (``platform_allows``)."""
    sd = sounddevice_module if sounddevice_module is not None else _import_sounddevice()
    if sd is None or not _has_input_device(sd):
        return False
    return platform_allows(platform, plist_declares=plist_declares)


class SlicerAudioCapture:
    """One microphone, one clip at a time.

    ``start()`` resets ``_frames`` before the stream exists, and sets ``_stream`` only once
    it has actually started: a ``cancel()`` in that narrow window no-ops (``_stream`` is
    still None) rather than stopping the stream that is about to start. Only ``Dictation``
    drives this object, and it never calls ``cancel()`` before ``start()`` has returned, so
    the window never opens in practice; a caller of its own would need to close it (install
    the stream under the lock before starting it)."""

    def __init__(self, sounddevice_module=None):
        self._sd = sounddevice_module if sounddevice_module is not None else _import_sounddevice()
        self._lock = threading.Lock()
        self._stream = None
        self._frames = []
        self._peak = 0
        self._total_bytes = 0

    def start(self):
        sd = self._sd
        if sd is None:
            raise CaptureError("capture_failed", "The sounddevice module is not installed.")
        if not _has_input_device(sd):
            raise CaptureError("no_microphone", "No microphone was found.")
        with self._lock:
            if self._stream is not None:
                return
            self._frames = []
            self._peak = 0
            self._total_bytes = 0
        try:
            stream = sd.RawInputStream(
                samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16", callback=self._on_frames
            )
            stream.start()
        except Exception as exc:
            raise CaptureError(_error_type(exc), "The microphone could not be opened ({}).".format(exc)) from exc
        with self._lock:
            self._stream = stream

    def _on_frames(self, indata, frames, time_info, status):
        # PortAudio's thread: gather, never block.
        chunk = bytes(indata)
        with self._lock:
            if self._total_bytes + len(chunk) > MAX_CLIP_BYTES:
                # A belt-and-braces cap of its own: talk2view.ui.dictation's
                # MAX_CLIP_SECONDS timer stops a clip long before this in production, but
                # this object must not grow without bound on its own if whatever is meant
                # to stop it does not (a hung router thread, a dead timer thread, or a
                # future host driving this object directly). CallbackStop tells PortAudio
                # to stop invoking this callback; the stream is not treated as errored, and
                # stop() still returns everything gathered up to this point.
                raise self._sd.CallbackStop()
            self._frames.append(chunk)
            self._total_bytes += len(chunk)
            self._peak = max(self._peak, peak_pcm16(chunk))

    def stop(self):
        stream, frames, peak = self._close()
        if stream is None:
            raise CaptureError("capture_failed", "Nothing was being recorded.")
        if peak < SILENCE_PEAK:
            raise CaptureError(
                "nothing_heard",
                "Nothing was heard. Check the microphone and Slicer's permission to use it.",
            )
        return pcm16_to_wav(b"".join(frames), SAMPLE_RATE, CHANNELS)

    def cancel(self):
        self._close()

    def level(self):
        with self._lock:
            if self._stream is None or not self._frames:
                return 0.0
            return min(1.0, peak_pcm16(self._frames[-1]) / 32768.0)

    def _close(self):
        with self._lock:
            stream, self._stream = self._stream, None
            frames, self._frames = self._frames, []
            peak, self._peak = self._peak, 0
            self._total_bytes = 0
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        return stream, frames, peak


def _error_type(exc):
    text = str(exc)
    if "-9999" in text or "Unanticipated host error" in text or "denied" in text.lower():
        return "microphone_denied"
    if "-9996" in text or "Invalid device" in text or "No Default Input Device" in text:
        return "no_microphone"
    return "capture_failed"


def make_capture():
    """A capture the host may hand to the UI server, or None when the mic cannot work here."""
    if not available():
        return None
    return SlicerAudioCapture()
