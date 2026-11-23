"""capture_view: real screenshots of the views, handed to the running chat."""

import shutil

import sf_testing  # noqa: F401  (isolates SLICERFLOW_HOME first)
import qt
import slicer

from sf_testing import UNREACHABLE_ENGINE, SceneTestCase, make_volume
from SlicerFlowLib import lifecycle, paths, user_tools
from SlicerFlowLib.tools import capture

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _size(png):
    return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")


class CaptureTest(SceneTestCase):
    def setUp(self):
        super().setUp()
        make_volume("CT")
        slicer.util.forceRenderAllViews()
        self.received = []
        capture.set_sink(lambda png, label: self.received.append((png, label)) or "kept " + label)

    def tearDown(self):
        capture.set_sink(None)
        lifecycle.stop()

    def test_each_view_gives_a_png_for_the_sink(self):
        for view, label in (("3d", "3D view"), ("Red", "Red slice view"), ("layout", "View layout")):
            self.assertEqual(capture.capture_view(view), "kept " + label)
        self.assertEqual([label for _png, label in self.received], ["3D view", "Red slice view", "View layout"])
        for png, _label in self.received:
            self.assertTrue(png.startswith(PNG_SIGNATURE))
            width, height = _size(png)
            self.assertTrue(0 < max(width, height) <= capture.MAX_SIDE, (width, height))

    def test_a_large_widget_is_scaled_down(self):
        widget = qt.QWidget()
        widget.resize(3200, 400)
        self.assertEqual(_size(capture.grab_png(widget)), (1600, 200))

    def test_a_view_outside_the_layout_is_refused(self):
        slicer.app.layoutManager().setLayout(slicer.vtkMRMLLayoutNode.SlicerLayoutOneUpRedSliceView)
        with self.assertRaisesRegex(ValueError, "not in the current layout"):
            capture.capture_view("green")

    def test_unknown_view_and_no_chat(self):
        with self.assertRaisesRegex(ValueError, "Unknown view"):
            capture.capture_view("blue")
        capture.set_sink(None)
        with self.assertRaisesRegex(ValueError, "chat to be running"):
            capture.capture_view("3d")

    def test_the_running_chat_takes_the_screenshots(self):
        from SlicerFlowLib.runtime import Runtime

        capture.set_sink(None)
        lifecycle.start(Runtime("pk_test_capture", UNREACHABLE_ENGINE, lambda url: None))
        self.assertIn("next message", capture.capture_view("red"))
        lifecycle.stop()
        with self.assertRaisesRegex(ValueError, "chat to be running"):
            capture.capture_view("red")

    def test_the_runtime_drops_the_screenshots_on_any_account_change(self):
        """A sign-out forgets the conversation, and signing back in as the same person
        keeps the fresh one, so the user id reads the same either side. Only the auth event
        itself can tell a batch in flight that its conversation has gone."""
        from SlicerFlowLib.runtime import Runtime

        capture.set_sink(None)
        runtime = Runtime("pk_test_capture", UNREACHABLE_ENGINE, lambda url: None)
        lifecycle.start(runtime)
        follow_ups = runtime.follow_ups
        self.assertIn(follow_ups.on_account_change, runtime.t2v.auth._listeners)
        epoch = follow_ups._epoch
        runtime.t2v.auth._notify_listeners(None)  # the SDK's own sign-out hook
        self.assertNotEqual(follow_ups._epoch, epoch)
        # ...and stopping takes the listener back out, so a closed queue is not still
        # reachable from the client.
        lifecycle.stop()
        self.assertNotIn(follow_ups.on_account_change, runtime.t2v.auth._listeners)

    def test_a_copy_of_the_pack_sends_to_the_same_sink(self):
        """An edited capture pack in the user's tools folder runs as a module of its own;
        its capture_view must still hand the screenshot to the running chat."""
        folder = user_tools.ensure_user_dir(paths.user_tools_dir())
        path = user_tools.pack_path(folder, "capture_copy")
        shutil.copyfile(capture.__file__, path)
        pack = user_tools.load_pack("capture_copy", path, {})
        self.assertIsNone(pack.error)
        self.assertEqual(pack.tools[0]("3d"), "kept 3D view")
        self.assertEqual([label for _png, label in self.received], ["3D view"])
