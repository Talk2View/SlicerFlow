"""The mic capability inside this Slicer: the host's audio() and the page's capabilities
agree with SlicerFlowLib.audio.available(); no thread of the capture is Slicer's main
thread; the probe of test_audio_probe.py says what a raw open would have done."""

import sys
import threading
import unittest

import sf_testing  # noqa: F401  (isolates SLICERFLOW_HOME first)

from fake_engine import FakeEngine
from sf_testing import wait_until
from SlicerFlowLib import audio, lifecycle, sdk


class AudioCapabilityTest(unittest.TestCase):
    def setUp(self):
        self.engine = FakeEngine().start()
        self.addCleanup(self.engine.stop)
        self.addCleanup(lifecycle.stop)

    def test_the_capability_matches_available(self):
        from SlicerFlowLib.runtime import Runtime

        runtime = Runtime("pk_test_audio", self.engine.url, lambda url: None)
        lifecycle.start(runtime)
        expected = sdk.ensure_sounddevice() and audio.available()
        self.assertEqual(runtime.host.audio() is not None, expected)
        wait_until(lambda: runtime.server._hub.snapshot()["state"].get("capabilities") is not None,
                   timeout_s=10, what="the capabilities slice")
        state = runtime.server._hub.snapshot()["state"]
        self.assertEqual(state["capabilities"]["audio"], expected)
        self.assertEqual(state["options"]["features"]["dictation"], expected)
        if sys.platform == "darwin" and not audio.bundle_declares_microphone():
            self.assertFalse(expected, "the official Slicer.app declares no microphone: no mic")

    def test_no_sounddevice_install_where_the_platform_rules_the_mic_out(self):
        if audio.platform_allows():
            self.skipTest("this platform allows the mic")
        from SlicerFlowLib.runtime import Runtime

        calls = []
        original = sdk.ensure_sounddevice
        sdk.ensure_sounddevice = lambda **kwargs: calls.append(kwargs) or True
        self.addCleanup(setattr, sdk, "ensure_sounddevice", original)
        self.assertIsNone(Runtime._capture())
        self.assertEqual(calls, [])

    def test_the_capture_never_records_on_the_main_thread(self):
        if not (sdk.ensure_sounddevice() and audio.available()):
            self.skipTest("no usable microphone here")
        capture = audio.SlicerAudioCapture()
        threads = []
        original = capture._on_frames

        def spy(*args):
            threads.append(threading.current_thread())
            original(*args)

        capture._on_frames = spy
        worker = threading.Thread(target=capture.start)
        worker.start()
        worker.join(10)
        wait_until(lambda: threads, timeout_s=5, what="a PortAudio callback")
        capture.cancel()
        self.assertNotIn(threading.main_thread(), threads)
