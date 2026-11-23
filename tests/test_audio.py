"""SlicerFlowLib.audio without Slicer or a microphone: a fake sounddevice module."""

import struct
import threading

import pytest

from talk2view.audio import CaptureError

from SlicerFlowLib import audio


class FakeStream:
    def __init__(self, samples, **kwargs):
        self.kwargs = kwargs
        self.samples = samples
        self.started = threading.Event()
        self.closed = False
        self.callback = kwargs["callback"]

    def start(self):
        self.started.set()
        # PortAudio's own thread: the callback never runs on the caller's.
        def feed():
            for chunk in self.samples:
                try:
                    self.callback(chunk, len(chunk) // 2, None, None)
                except Exception:
                    # Real PortAudio stops invoking the callback after CallbackStop or
                    # CallbackAbort (and would otherwise treat any other exception as an
                    # error and abort the stream); either way, no further chunk is fed.
                    return
        threading.Thread(target=feed, daemon=True).start()

    def stop(self):
        pass

    def close(self):
        self.closed = True


class FakeSounddevice:
    class PortAudioError(Exception):
        pass

    class CallbackStop(Exception):
        pass

    def __init__(self, samples=(), devices=1, open_error=None):
        self.samples = list(samples)
        self.devices = devices
        self.open_error = open_error
        self.streams = []
        self.default = type("d", (), {"device": (0 if devices else -1, 0)})()

    def query_devices(self, device=None, kind=None):
        if kind == "input" and not self.devices:
            raise ValueError("no default input device")
        return {"name": "Fake mic", "max_input_channels": 1}

    def RawInputStream(self, **kwargs):
        if self.open_error is not None:
            raise self.open_error
        stream = FakeStream(self.samples, **kwargs)
        self.streams.append(stream)
        return stream


TONE = struct.pack("<4h", 0, 5000, -5000, 0)
SILENCE = struct.pack("<4h", 0, 0, 0, 0)


def test_available_needs_the_module_a_device_and_on_macos_the_plist_key():
    sd = FakeSounddevice()
    assert audio.available("linux", sounddevice_module=sd) is True
    assert audio.available("win32", sounddevice_module=sd) is True
    assert audio.available("darwin", sounddevice_module=sd, plist_declares=True) is True
    assert audio.available("darwin", sounddevice_module=sd, plist_declares=False) is False
    assert audio.available("linux", sounddevice_module=FakeSounddevice(devices=0)) is False
    assert audio.available("linux", sounddevice_module=None) is False


def test_platform_allows_rules_out_only_macos_without_the_plist_key():
    assert audio.platform_allows("linux") is True
    assert audio.platform_allows("win32") is True
    assert audio.platform_allows("darwin", plist_declares=True) is True
    assert audio.platform_allows("darwin", plist_declares=False) is False


def test_start_stop_records_16k_mono_int16_off_the_calling_thread_and_returns_wav():
    sd = FakeSounddevice(samples=[TONE, TONE])
    capture = audio.SlicerAudioCapture(sd)
    capture.start()
    stream = sd.streams[0]
    assert stream.kwargs["samplerate"] == 16000 and stream.kwargs["channels"] == 1 and stream.kwargs["dtype"] == "int16"
    assert stream.started.wait(2)
    import time
    time.sleep(0.05)
    assert 0.0 < capture.level() <= 1.0
    data = capture.stop()
    assert data[:4] == b"RIFF" and stream.closed
    assert capture.level() == 0.0


def test_the_capture_stops_appending_once_the_clip_exceeds_max_clip_bytes(monkeypatch):
    from talk2view.audio import pcm16_to_wav

    # A cap of exactly one chunk's worth: the first chunk fits, the second pushes the
    # total over it and must raise CallbackStop from inside the callback rather than being
    # appended -- a belt-and-braces bound of the capture's own, independent of whatever else
    # (talk2view.ui.dictation's own timer, in production) is meant to stop the clip first.
    monkeypatch.setattr(audio, "MAX_CLIP_BYTES", len(TONE))
    sd = FakeSounddevice(samples=[TONE, TONE, TONE, TONE])
    capture = audio.SlicerAudioCapture(sd)
    capture.start()
    stream = sd.streams[0]
    assert stream.started.wait(2)
    import time
    time.sleep(0.05)
    data = capture.stop()
    # Exactly the first chunk made it in: the clip never grew past the cap, and stop()
    # still hands back everything gathered before it was reached.
    assert data == pcm16_to_wav(TONE, audio.SAMPLE_RATE, audio.CHANNELS)
    assert stream.closed


def test_a_silent_clip_is_nothing_heard_and_cancel_drops_it():
    sd = FakeSounddevice(samples=[SILENCE, SILENCE])
    capture = audio.SlicerAudioCapture(sd)
    capture.start()
    import time
    time.sleep(0.05)
    with pytest.raises(CaptureError) as refused:
        capture.stop()
    assert refused.value.type == "nothing_heard"
    capture.start()
    capture.cancel()
    assert sd.streams[-1].closed
    capture.cancel()  # not recording: a no-op


def test_an_open_failure_is_a_capture_error():
    sd = FakeSounddevice(open_error=FakeSounddevice.PortAudioError("Error opening RawInputStream: Unanticipated host error [PaErrorCode -9999]"))
    with pytest.raises(CaptureError) as refused:
        audio.SlicerAudioCapture(sd).start()
    assert refused.value.type == "microphone_denied"
    none = FakeSounddevice(devices=0)
    with pytest.raises(CaptureError) as missing:
        audio.SlicerAudioCapture(none).start()
    assert missing.value.type == "no_microphone"


def test_bundle_declares_microphone_reads_a_plist(tmp_path):
    import plistlib
    with_key = tmp_path / "with.plist"
    with_key.write_bytes(plistlib.dumps({"NSMicrophoneUsageDescription": "Dictation"}))
    without = tmp_path / "without.plist"
    without.write_bytes(plistlib.dumps({"CFBundleName": "Slicer"}))
    assert audio.bundle_declares_microphone(str(with_key)) is True
    assert audio.bundle_declares_microphone(str(without)) is False
    assert audio.bundle_declares_microphone(str(tmp_path / "missing.plist")) is False
