"""What opening the microphone does in this 3D Slicer, on this platform: recorded to
``build/slicer-tests/mic-probe.json`` for reference, and asserted against the rule
SlicerFlowLib.audio applies (macOS: no ``NSMicrophoneUsageDescription`` in the bundle's
Info.plist, no mic).

The open runs in a CHILD process of the bundle's own Python launcher, so a termination
during it cannot end this suite. Only ``audio.available()`` -- the gate the rest of the
app relies on -- is asserted; the child's own outcome is recorded, never asserted, because
it cannot prove which way the open went. Two things break the assumption that the child is
simply Slicer speaking for itself:

- ``Contents/bin/PythonSlicer`` is a different executable from ``Contents/MacOS/Slicer``,
  and PortAudio does not go through the AVFoundation-style client check the
  usage-description crash is enforced by; the module's own dictation (``audio.py``) opens
  the device in-process, where neither of those apply either.
- TCC attributes a subprocess's request to whatever process is *responsible* for it. Under
  this repo's own runner (``scripts/slicer-tests.sh``, ``open -n -W``) that is this Slicer
  instance, but under a terminal, an IDE or an agent's shell it is that ancestor, whose own
  microphone grant (if it has one) the child inherits regardless of Slicer's bundle.

So a clean, zero-code open proves nothing on its own: it is consistent with a real grant
*or* a silently denied device, since macOS delivers silence rather than an error to a
denied client. To tell those apart the child also reads about half a second of audio and
reports its peak level: ``OPENED peak=0`` is the silent-denial signature, ``OPENED
peak>0`` is a real grant from somewhere (Slicer's own, or inherited), and a signal or a
non-zero exit is a kill or a PortAudio refusal.

Either way, without the usage-description key macOS can never show a prompt for Slicer, so
the microphone stays unusable regardless: a device that delivers silence forever (which
the chat would report as ``nothing_heard`` on every attempt), or the documented crash. The
end-user cannot repair either from System Settings, because an app that has never prompted
is never listed there. Verifying which of the two actually happens needs a real
double-click launch, not this child -- see "Microphone" in ``README.md``.
"""

import json
import os
import plistlib
import subprocess
import sys
import unittest

import sf_testing  # noqa: F401  (isolates SLICERFLOW_HOME first)
import slicer

OUT = os.path.abspath(os.environ.get("SF_TEST_OUT") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "build", "slicer-tests"))

CHILD = r"""
import struct
import sys
try:
    import sounddevice as sd
except Exception as exc:
    print("IMPORT-FAILED", type(exc).__name__, exc)
    sys.exit(2)
try:
    stream = sd.RawInputStream(samplerate=16000, channels=1, dtype="int16")
    stream.start()
    frames, _overflowed = stream.read(8000)  # ~0.5 s at 16 kHz; blocks until captured
    stream.stop(); stream.close()
    data = bytes(frames)
    count = len(data) // 2
    peak = max((abs(s) for s in struct.unpack("<{}h".format(count), data[: count * 2])), default=0)
    print("OPENED peak={}".format(peak))
    sys.exit(0)
except Exception as exc:
    print("OPEN-FAILED", type(exc).__name__, exc)
    sys.exit(3)
"""


def bundle_plist():
    """The app bundle's Info.plist on macOS (``<Slicer.app>/Contents/Info.plist``), else None."""
    if sys.platform != "darwin":
        return None
    home = slicer.app.slicerHome  # <Slicer.app>/Contents
    path = os.path.join(home, "Info.plist")
    if not os.path.isfile(path):
        path = os.path.join(os.path.dirname(home), "Info.plist")
    with open(path, "rb") as fh:
        return plistlib.load(fh)


class MicrophoneProbeTest(unittest.TestCase):
    def test_a_raw_open_is_recorded_and_the_gate_agrees_with_it(self):
        from SlicerFlowLib import audio, sdk

        sdk.ensure_sounddevice()
        plist = bundle_plist()
        has_key = bool(plist and plist.get("NSMicrophoneUsageDescription"))
        launcher = os.path.join(slicer.app.slicerHome, "bin", "PythonSlicer" + (".exe" if os.name == "nt" else ""))
        try:
            result = subprocess.run([launcher, "-c", CHILD], capture_output=True, text=True, timeout=60)
            returncode, stdout, stderr = result.returncode, result.stdout, result.stderr
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            # A stream that never delivers the frames read() waits for (a device wedged,
            # rather than silently denied -- see peak=0 above) hangs the child rather than
            # returning: recorded as its own outcome, never left to error the test.
            returncode, stdout, stderr = None, exc.stdout, exc.stderr
            timed_out = True
        record = {
            "platform": sys.platform,
            "plistKey": has_key,
            "returncode": returncode,
            "timedOut": timed_out,
            "stdout": (stdout or "").strip()[-400:],
            "stderrTail": (stderr or "").strip()[-800:],
        }
        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, "mic-probe.json"), "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2)
        print("[mic-probe]", json.dumps(record))
        # The child's own outcome is recorded above, never asserted here (see the module
        # docstring: it cannot tell "Slicer was granted" from "the launching ancestor
        # already had access", and a denied mic on macOS delivers silence, not an error).
        # Only the gate -- the one contract the rest of the app actually relies on -- is
        # checked. A timeout (returncode None) falls to the same "no mic" branch as any
        # other non-zero outcome.
        if sys.platform == "darwin" and not has_key:
            self.assertFalse(audio.available(), record)
        elif returncode == 0:
            self.assertTrue(audio.available(), record)
        else:
            self.assertFalse(audio.available(), record)
