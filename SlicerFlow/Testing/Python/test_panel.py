"""The module panel: where the partner key comes from, and Start/Stop of the chat that
the chat panel (the dock on the right) shows."""

import os
import unittest

import sf_testing  # noqa: F401  (isolates SLICERFLOW_HOME first)
import qt
import slicer

from sf_testing import UNREACHABLE_ENGINE
from SlicerFlowLib import config, lifecycle, paths, sdk


def _lock_file_holding(hostname, pid="999999"):
    """A lock file like Qt's, written at the module's real lock path, held by ``pid`` on
    ``hostname``. The machine id is not this one, so Qt cannot check whether that process
    lives and refuses the lock (see ``lifecycle.ChatLock``'s docstring); that is the one
    refusal the chat panel can offer to clear."""
    path = paths.lock_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{}\nSlicer\n{}\n{}\n{}\n".format(pid, hostname, "0" * 32, "1" * 32))
    return path


def _settle():
    for _ in range(20):
        slicer.app.processEvents()


class PanelTest(unittest.TestCase):
    def setUp(self):
        self._saved_env = {k: os.environ.pop(k) for k in (config.ENV_KEY, config.ENV_URL) if k in os.environ}
        self.widget = slicer.modules.slicerflow.widgetRepresentation().self()

    def tearDown(self):
        lifecycle.stop()
        self.widget.startButton.text = "Start chat"
        for name in (config.ENV_KEY, config.ENV_URL):
            os.environ.pop(name, None)
        os.environ.update(self._saved_env)

    def test_the_sdk_is_the_pinned_release(self):
        if os.environ.get("SF_SKIP_SDK_INSTALL") == "1":
            self.skipTest("SF_SKIP_SDK_INSTALL=1: the runner left Slicer's SDK as it was")
        self.assertFalse(sdk.needs_install())

    def test_the_panel_uses_the_extension_key_by_default(self):
        bundled = config.load_bundled(os.path.join(sf_testing.MODULE_DIR, "Resources", "partner.json"))
        connection = self.widget.connection()
        self.assertEqual(connection.partner_key, bundled["partnerKey"].strip())
        self.assertEqual(connection.engine_url, config.DEFAULT_ENGINE_URL)

    def test_start_refuses_when_there_is_no_partner_key(self):
        """``Resources/partner.json`` ships an empty key until Talk2View issues
        SlicerFlow's own, so this refusal is the first thing every user of this build
        meets. Without it Start chat goes ahead and fails later, obscurely."""
        self.widget.connection = lambda: config.Connection("", UNREACHABLE_ENGINE, "")
        self.addCleanup(self.widget.__dict__.pop, "connection", None)
        self.widget.onStartStop()
        self.assertIsNone(lifecycle.current())
        self.assertIn("partner key", self.widget.statusLabel.text)

    def test_a_plain_http_engine_is_refused(self):
        os.environ[config.ENV_KEY] = "pk_test_panel"
        os.environ[config.ENV_URL] = "http://engine.example.com"
        self.widget.onStartStop()
        self.assertIsNone(lifecycle.current())
        self.assertIn("https://", self.widget.statusLabel.text)

    def test_start_and_stop_with_an_environment_override(self):
        if sdk.needs_install():
            # Start would pip-install the pinned SDK first and re-import the library in the
            # middle of the run, which breaks the test files loaded before it.
            self.skipTest("the installed SDK is not the pinned release")
        os.environ[config.ENV_KEY] = "pk_test_panel"
        os.environ[config.ENV_URL] = UNREACHABLE_ENGINE
        self.widget.onStartStop()
        self.assertIsNotNone(lifecycle.current())
        self.assertEqual(self.widget.startButton.text, "Stop chat")
        self.widget.onStartStop()
        self.assertIsNone(lifecycle.current())
        self.assertEqual(self.widget.startButton.text, "Start chat")

    def test_being_busy_disables_every_button(self):
        buttons = (self.widget.startButton,)
        self.addCleanup(self.widget._setBusy, False)
        self.widget._setBusy(True)
        for button in buttons:
            self.assertFalse(button.enabled, button.text)
        self.widget._setBusy(False)
        for button in buttons:
            self.assertTrue(button.enabled, button.text)

    def test_a_lock_left_on_another_computer_is_left_in_place_and_named(self):
        """Start chat from this panel never removes the lock by itself: the chat panel
        offers that (test_chat_dock.py), and this panel repeats what it says."""
        if sdk.needs_install():
            self.skipTest("the installed SDK is not the pinned release")
        path = _lock_file_holding("another-computer")
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        os.environ[config.ENV_KEY] = "pk_test_panel"
        os.environ[config.ENV_URL] = UNREACHABLE_ENGINE
        self.widget.onStartStop()
        self.assertIsNone(lifecycle.current())
        self.assertEqual(self.widget.startButton.text, "Start chat")
        self.assertTrue(os.path.exists(path))
        self.assertIn("another-computer", self.widget.statusLabel.text)

    def test_after_switching_modules_and_back_nothing_is_drawn_over_anything(self):
        """Slicer leaks a module's layout item when switching modules away, and the item
        the module gets back caches its first height-for-width for good: a panel that
        reports one was given its old height, and a pack list covered the lines under it."""
        previous = slicer.util.moduleSelector().selectedModule
        self.addCleanup(slicer.util.selectModule, previous or "Welcome")
        section = self.widget.toolsSection
        self.addCleanup(setattr, section.box, "collapsed", section.box.collapsed)
        section.box.collapsed = True
        for module in ("SlicerFlow", "Data", "SlicerFlow"):
            slicer.util.selectModule(module)
            _settle()
        section.box.collapsed = False
        _settle()
        section.packTree.setCurrentItem(section.packTree.topLevelItem(0))
        _settle()
        panel = slicer.modules.slicerflow.widgetRepresentation()
        self.assertFalse(panel.hasHeightForWidth())
        shown = [w for w in (section.packTree, section.detailLabel, section.toolTree,
                             section.editButton, section.newButton) if w.isVisible()]
        self.assertGreaterEqual(len(shown), 4)
        for upper, lower in zip(shown, shown[1:]):
            self.assertLess(upper.mapTo(panel, qt.QPoint(0, upper.height)).y(),
                            lower.mapTo(panel, qt.QPoint(0, 0)).y() + 1)
        for label in panel.findChildren(qt.QLabel):
            if label.isVisible() and label.wordWrap and label.text:
                self.assertGreaterEqual(label.height, label.heightForWidth(label.width), label.text)

    def test_the_header_names_the_product(self):
        self.assertEqual(self.widget.titleLabel.text, "SlicerFlow")
        self.assertIn("Talk2View", self.widget.subtitleLabel.text)

    def test_the_status_line_says_what_the_chat_is_doing(self):
        line = self.widget._statusLine
        self.assertEqual(line(True, ""), ("Chat running", "ok"))
        self.assertEqual(line(False, "Starting the chat…"), ("Starting the chat…", "busy"))
        self.assertEqual(line(False, "Chat stopped."), ("Chat stopped", "idle"))
        self.assertEqual(line(False, "Could not start the chat: boom"),
                         ("Could not start the chat: boom", "problem"))
        self.assertEqual(line(True, "Talk2View can't be reached."),
                         ("Talk2View can't be reached.", "problem"))
        self.assertEqual(line(False, ""), ("Chat stopped", "idle"))
        # Never started: the notice of what Start chat sends is news, not a problem.
        self.assertEqual(line(False, config.CONSENT_NOTICE), (config.CONSENT_NOTICE, "idle"))

    def test_a_refused_start_shows_a_problem(self):
        self.widget.connection = lambda: config.Connection("", UNREACHABLE_ENGINE, "")
        self.addCleanup(self.widget.__dict__.pop, "connection", None)
        self.widget.onStartStop()
        self.assertEqual(self.widget.statusState, "problem")

    def test_start_is_the_primary_action_and_stop_is_not(self):
        self.widget._styleStartButton(False)
        self.assertIn("background-color", self.widget.startButton.styleSheet)
        self.widget._styleStartButton(True)
        self.assertNotIn("background-color", self.widget.startButton.styleSheet)

    def test_the_header_uses_the_brand_mark_and_typeface(self):
        pixmap = self.widget.logoLabel.pixmap
        self.assertFalse(pixmap.isNull())
        ratio = pixmap.width() / pixmap.height()
        self.assertAlmostEqual(ratio, 112 / 82, places=1)  # the logomark, not the square app icon
        self.assertEqual(self.widget.titleLabel.font.family(), "Space Grotesk")
        self.assertEqual(self.widget.startButton.font.family(), "Space Grotesk")

    def test_start_is_square_and_teal_like_the_brand_buttons(self):
        self.widget._styleStartButton(False)
        style = self.widget.startButton.styleSheet
        self.assertIn("border-radius: 0", style)
        self.assertIn("#26c8b8", style.lower())
        self.assertIn("#021c25", style.lower())  # Smoke text: white on Teal is too faint

    def test_state_colours_are_the_brands(self):
        from SlicerFlowLib import panel_style

        light, dark = qt.QLabel(), qt.QLabel()
        palette = dark.palette
        palette.setColor(qt.QPalette.Window, qt.QColor("#021C25"))
        palette.setColor(qt.QPalette.WindowText, qt.QColor("#F2F0EC"))
        dark.setPalette(palette)
        palette = light.palette
        palette.setColor(qt.QPalette.Window, qt.QColor("#FFFFFF"))
        light.setPalette(palette)
        expected = {"ok": ("#26c8b8", "#26c8b8"), "busy": ("#90642c", "#f0a74a"),
                    "problem": ("#bb524e", "#d69795")}
        for state, (on_light, on_dark) in expected.items():
            self.assertEqual(panel_style.state_color(light, state).name(), on_light, state)
            self.assertEqual(panel_style.state_color(dark, state).name(), on_dark, state)
        self.assertEqual(panel_style.dot_color(light, "idle").name(), "#9aa4a8")  # Stone
