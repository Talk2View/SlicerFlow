"""The chat panel: the "SlicerFlow by Talk2View" dock on the right of the main window,
which holds the chat's web view, starts by itself outside tests, and outlives being
closed."""

import json
import logging
import os
import sys
import time
import unittest
import urllib.request
import uuid

import sf_testing  # noqa: F401  (isolates SLICERFLOW_HOME first)
import qt
import slicer

from fake_engine import FakeEngine
from sf_testing import UNREACHABLE_ENGINE, wait_until
from SlicerFlowLib import chat_dock, config, lifecycle, paths, sdk

TEST_CONNECTION = config.Connection("pk_test_dock", UNREACHABLE_ENGINE, "settings")


def _web_url(web):
    value = web.url
    if callable(value):
        value = value()
    return str(value.toString() if hasattr(value, "toString") else value)


def _docks():
    return slicer.util.findChildren(slicer.util.mainWindow(), name=chat_dock.OBJECT_NAME)


def _pump(seconds):
    """Run the event loop for ``seconds``: for the things that must NOT happen."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        slicer.app.processEvents()
        time.sleep(0.01)


def _lock_file_holding(hostname, pid="999999"):
    """A lock file like Qt's at the module's real lock path, held by ``pid`` on
    ``hostname`` (see test_panel.py)."""
    path = paths.lock_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{}\nSlicer\n{}\n{}\n{}\n".format(pid, hostname, "0" * 32, "1" * 32))
    return path


def _records(test):
    """Every log record from here on, at any level, from any logger."""
    records = []

    class Recorder(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Recorder(level=logging.DEBUG)
    for name in (None, "SlicerFlow"):
        logger = logging.getLogger(name)
        logger.addHandler(handler)
        test.addCleanup(logger.removeHandler, handler)
        previous = logger.level
        logger.setLevel(logging.DEBUG)
        test.addCleanup(logger.setLevel, previous)
    return records


class ChatDockTest(unittest.TestCase):
    def setUp(self):
        self.dock = chat_dock.ensure()
        self.dock.show()

    def tearDown(self):
        lifecycle.stop()
        if chat_dock.current() is not None:
            chat_dock.current().show()

    def needs_the_pinned_sdk(self):
        if sdk.needs_install():
            self.skipTest("the installed SDK is not the pinned release")

    def environment(self, **values):
        """Set (or, with None, unset) environment variables for this test only."""
        for name, value in values.items():
            saved = os.environ.get(name)
            self.addCleanup(
                lambda n=name, v=saved: os.environ.__setitem__(n, v) if v is not None
                else os.environ.pop(n, None))
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def consent(self, value):
        """Whether the user has started the chat before, for this test only."""
        settings = qt.QSettings()
        saved = settings.value(config.SETTINGS_CONSENTED)
        self.addCleanup(
            lambda: settings.setValue(config.SETTINGS_CONSENTED, saved) if saved is not None
            else settings.remove(config.SETTINGS_CONSENTED))
        if value is None:
            settings.remove(config.SETTINGS_CONSENTED)
        else:
            settings.setValue(config.SETTINGS_CONSENTED, value)

    # ── The panel ──

    def test_the_panel_is_built_once_docked_right_with_the_web_view(self):
        self.assertIs(chat_dock.ensure(), self.dock)
        self.assertEqual(len(_docks()), 1)
        window = slicer.util.mainWindow()
        self.assertEqual(window.dockWidgetArea(self.dock.dock), qt.Qt.RightDockWidgetArea)
        self.assertEqual(self.dock.dock.windowTitle, "SlicerFlow by Talk2View")
        webs = slicer.util.findChildren(self.dock.dock, className="qSlicerWebWidget")
        self.assertEqual(len(webs), 1)

    def test_the_panel_can_narrow_to_260_px(self):
        # Conventional layout's slice views need 929 px and the module panel up to 401: on a
        # 1728 px laptop screen the chat's minimum decides whether the module panel can move.
        self.assertEqual(self.dock.dock.minimumSizeHint.width(), chat_dock.MIN_WIDTH)
        self.assertEqual(chat_dock.MIN_WIDTH, 260)

    def test_its_toggle_is_in_the_view_menu(self):
        menu = slicer.util.findChildren(slicer.util.mainWindow(), name="ViewMenu")[0]
        toggles = [a for a in menu.actions() if a.text == "SlicerFlow by Talk2View"]
        self.assertEqual(len(toggles), 1)
        toggles[0].trigger()
        self.assertFalse(self.dock.shown)
        toggles[0].trigger()
        self.assertTrue(self.dock.shown)

    def test_opening_the_module_builds_no_web_view_there(self):
        widget = slicer.modules.slicerflow.widgetRepresentation()
        self.assertEqual(slicer.util.findChildren(widget, className="qSlicerWebWidget"), [])
        self.assertIs(widget.self().chatDock, chat_dock.current())
        self.assertEqual(len(_docks()), 1)

    # ── Starting and stopping ──

    def test_starting_shows_the_chat_in_the_panel(self):
        self.needs_the_pinned_sdk()
        self.assertIsNone(self.dock.start(TEST_CONNECTION))
        url = lifecycle.current().server.url
        wait_until(lambda: _web_url(self.dock.web) == url, what="the panel to show the chat")

    def test_starting_shows_the_dock_when_it_was_hidden(self):
        """There is no "Show chat panel" button any more: Start chat is what reopens a
        closed dock (``ChatDock.start`` shows it before it does anything else)."""
        self.needs_the_pinned_sdk()
        self.dock.dock.hide()
        self.assertFalse(self.dock.shown)
        self.assertIsNone(self.dock.start(TEST_CONNECTION))
        self.assertTrue(self.dock.shown)

    def test_stop_blanks_the_panel_and_offers_to_start_again(self):
        self.needs_the_pinned_sdk()
        self.dock.start(TEST_CONNECTION)
        self.dock.stop()
        self.assertIsNone(lifecycle.current())
        self.assertEqual(self.dock.actionButton.text, "Start chat")
        self.dock.actionButton.click()
        self.assertIsNotNone(lifecycle.current())

    def test_closing_the_panel_leaves_the_chat_running_and_is_remembered(self):
        self.needs_the_pinned_sdk()
        self.dock.start(TEST_CONNECTION)
        runtime = lifecycle.current()
        self.dock.dock.close()
        _pump(0.2)
        self.assertIs(lifecycle.current(), runtime)
        self.assertFalse(config.setting_is_true(
            qt.QSettings().value(config.SETTINGS_PANEL_SHOWN), True))
        self.dock.show()
        self.assertTrue(self.dock.shown)
        self.assertTrue(config.setting_is_true(
            qt.QSettings().value(config.SETTINGS_PANEL_SHOWN), False))
        self.assertIs(lifecycle.current(), runtime)

    def test_the_panel_says_when_the_engine_cannot_be_reached(self):
        self.needs_the_pinned_sdk()
        self.dock.start(TEST_CONNECTION)
        wait_until(lambda: "cannot reach" in self.dock.status,
                   what="the panel to say the engine cannot be reached")
        self.assertIn(UNREACHABLE_ENGINE, self.dock.status)
        self.assertEqual(self.dock.actionButton.text, "Try again")
        runtime = lifecycle.current()
        self.dock.actionButton.click()  # checks again; the chat carries on
        self.assertIs(lifecycle.current(), runtime)
        self.assertIn("Checking", self.dock.status)
        wait_until(lambda: "cannot reach" in self.dock.status,
                   what="the check to fail again")
        self.assertIs(lifecycle.current(), runtime)

    def test_the_engine_line_clears_when_the_engine_answers_or_someone_signs_in(self):
        self.needs_the_pinned_sdk()
        from talk2view import NetworkError

        engine = FakeEngine().start()
        self.addCleanup(engine.stop)
        self.addCleanup(lambda: os.path.exists(paths.token_file())
                        and os.remove(paths.token_file()))
        self.dock.start(config.Connection("pk_test_reach", engine.url, "settings"))
        runtime = lifecycle.current()
        wait_until(lambda: engine.bodies("/v1/tools/register"), what="the first registration")
        _pump(0.2)  # its answer, posted to this thread
        # As if the engine had been out when the chat started, and is back now.
        self.dock._unreachable(NetworkError("Network request failed: test"))
        self.assertIn("cannot reach", self.dock.status)
        self.dock.actionButton.click()  # Try again
        wait_until(lambda: self.dock.status == "", what="the line to clear when the engine answers")
        self.assertIs(lifecycle.current(), runtime)

        self.dock._unreachable(NetworkError("Network request failed: test"))
        runtime.t2v.auth.login("user@example.com", "secret")
        wait_until(lambda: self.dock.status == "", what="the line to clear on a sign-in")

    def test_a_check_that_fails_some_other_way_still_offers_try_again(self):
        """Not only a NetworkError: whatever ends the check, the panel must not be left on
        "Checking the connection…" with no button."""
        self.needs_the_pinned_sdk()
        self.dock.start(TEST_CONNECTION)
        wait_until(lambda: "cannot reach" in self.dock.status, what="the first check")
        runtime = lifecycle.current()

        def broken(tools):
            raise RuntimeError("something unexpected")

        runtime.t2v.tools.register_from_functions = broken
        self.dock.actionButton.click()
        wait_until(lambda: "something unexpected" in self.dock.status,
                   what="the failed check to be told")
        self.assertEqual(self.dock.actionButton.text, "Try again")
        self.assertIs(lifecycle.current(), runtime)

    def test_a_new_panel_says_the_chat_is_stopped(self):
        """After a Reload, or when the chat does not start by itself, the panel is not a
        blank square: it says so, and offers to start."""
        self.needs_the_pinned_sdk()
        self.consent(True)
        chat_dock.remove()
        self.dock = chat_dock.ensure()
        self.assertEqual(self.dock.status, "Chat stopped.")
        self.assertFalse(self.dock.privacyLink.visible)
        self.assertTrue(self.dock.statusRow.visible)
        self.assertEqual(self.dock.actionButton.text, "Start chat")
        self.dock.actionButton.click()
        self.assertIsNotNone(lifecycle.current())

    def test_no_panel_without_a_main_window(self):
        dock = chat_dock.current()
        self.addCleanup(setattr, slicer.modules, chat_dock.DOCK_ATTR, dock)
        setattr(slicer.modules, chat_dock.DOCK_ATTR, None)
        main_window = slicer.util.mainWindow
        self.addCleanup(setattr, slicer.util, "mainWindow", main_window)
        slicer.util.mainWindow = lambda *args, **kwargs: None
        self.assertIsNone(chat_dock.ensure())
        sys.modules["SlicerFlow"].onStartupCompleted(
            testing=False, options=config.command_options(object()))
        self.assertIsNone(chat_dock.current())

    # ── Starting by itself ──

    def test_the_module_hooks_slicer_startup_once(self):
        """What starts the chat outside tests: the module connects ``startupCompleted``
        when Slicer builds it (counted, as the quit hook is in test_lifecycle.py)."""
        hook = sys.modules["SlicerFlow"].onStartupCompleted
        connections = 0
        while slicer.app.disconnect("startupCompleted()", hook):
            connections += 1
        self.addCleanup(slicer.app.connect, "startupCompleted()", hook)
        self.assertEqual(connections, 1)

    def test_the_chat_does_not_start_by_itself_in_testing_mode(self):
        self.assertTrue(slicer.app.testingEnabled())
        # Only testing mode stands in the way here: the runner's opt-out is lifted, and a
        # chat that did start would find a key and an engine.
        self.environment(**{config.ENV_NO_AUTOSTART: None, config.ENV_KEY: "pk_test_dock",
                            config.ENV_URL: UNREACHABLE_ENGINE})
        # No scripted-run option either: only testing mode is left to stop it.
        sys.modules["SlicerFlow"].onStartupCompleted(options=config.command_options(object()))
        _pump(0.5)
        self.assertIsNone(lifecycle.current())

    def test_the_chat_does_not_start_by_itself_in_a_scripted_run(self):
        """This Slicer runs the test runner with --python-script: outside testing mode that
        alone must keep the chat from starting (it would take the chat lock)."""
        self.assertTrue(slicer.app.commandOptions().pythonScript)
        self.environment(**{config.ENV_NO_AUTOSTART: None, config.ENV_KEY: "pk_test_dock",
                            config.ENV_URL: UNREACHABLE_ENGINE})
        sys.modules["SlicerFlow"].onStartupCompleted(testing=False)
        _pump(0.5)
        self.assertIsNone(lifecycle.current())

    def test_startup_starts_the_chat_when_nothing_stands_in_the_way(self):
        """The whole allowed path: Slicer's startup hook, the panel, the next turn, the chat,
        for a user who has started the chat before."""
        self.needs_the_pinned_sdk()
        self.consent(True)
        self.environment(**{config.ENV_NO_AUTOSTART: None, config.ENV_KEY: "pk_test_dock",
                            config.ENV_URL: UNREACHABLE_ENGINE})
        sys.modules["SlicerFlow"].onStartupCompleted(
            testing=False, options=config.command_options(object()))
        self.assertIsNone(lifecycle.current(), "started before the window could paint")
        wait_until(lambda: lifecycle.current() is not None, what="the chat to start by itself")
        wait_until(lambda: _web_url(chat_dock.current().web) == lifecycle.current().server.url,
                   what="the panel to show the chat")

    def test_autostart_waits_for_the_next_turn(self):
        """So Slicer's window paints before the chat starts, and it starts only once."""
        self.needs_the_pinned_sdk()
        self.consent(True)
        self.environment(**{config.ENV_KEY: "pk_test_dock", config.ENV_URL: UNREACHABLE_ENGINE})
        self.dock.enable_autostart()
        self.assertIsNone(lifecycle.current(), "started before the window could paint")
        wait_until(lambda: lifecycle.current() is not None, what="the chat to start by itself")
        # Once: stopping it and showing the panel again does not start it again.
        self.dock.stop()
        self.dock.dock.hide()
        self.dock.dock.show()
        _pump(0.3)
        self.assertIsNone(lifecycle.current())

    def test_autostart_waits_for_the_panel_to_be_shown(self):
        self.needs_the_pinned_sdk()
        self.consent(True)
        self.environment(**{config.ENV_KEY: "pk_test_dock", config.ENV_URL: UNREACHABLE_ENGINE})
        self.dock.enable_autostart()
        self.dock.dock.hide()  # closed before the next turn came
        _pump(0.3)
        self.assertIsNone(lifecycle.current(), "started while the panel was closed")
        self.dock.dock.show()
        wait_until(lambda: lifecycle.current() is not None,
                   what="the chat to start when the panel is shown")

    def test_a_closed_panel_starts_the_chat_when_first_shown(self):
        self.needs_the_pinned_sdk()
        self.consent(True)
        self.environment(**{config.ENV_KEY: "pk_test_dock", config.ENV_URL: UNREACHABLE_ENGINE})
        self.dock.dock.hide()
        self.dock.enable_autostart()
        _pump(0.3)
        self.assertIsNone(lifecycle.current(), "started while the panel was closed")
        self.dock.dock.show()
        self.assertIsNone(lifecycle.current(), "started before the window could paint")
        wait_until(lambda: lifecycle.current() is not None, what="the chat to start by itself")

    # ── The first start: the user's opt-in ──

    def test_before_the_first_start_nothing_starts_or_installs_or_is_sent(self):
        """Slicer's startup with no opt-in yet: the panel says what the chat sends and
        waits. No SDK install, no runtime (so no request to the engine)."""
        self.consent(None)
        engine = FakeEngine().start()
        self.addCleanup(engine.stop)
        self.environment(**{config.ENV_NO_AUTOSTART: None, config.ENV_KEY: "pk_test_dock",
                            config.ENV_URL: engine.url})
        installs = []
        for name in ("needs_install", "install"):
            original = getattr(sdk, name)
            self.addCleanup(setattr, sdk, name, original)
            setattr(sdk, name, lambda *args, n=name, **kwargs: installs.append(n))
        chat_dock.remove()
        sys.modules["SlicerFlow"].onStartupCompleted(
            testing=False, options=config.command_options(object()))
        _pump(0.5)
        dock = chat_dock.current()
        self.dock = dock
        self.assertIsNone(lifecycle.current())
        self.assertEqual(installs, [])
        self.assertEqual(engine.requests, [])
        self.assertTrue(dock.shown)
        self.assertEqual(dock.status, config.CONSENT_NOTICE)
        self.assertTrue(dock.privacyLink.visible)
        self.assertIn(config.PRIVACY_URL, dock.privacyLink.text)
        self.assertEqual(dock.actionButton.text, "Start chat")
        self.assertFalse(chat_dock.consented())

    def test_pressing_start_chat_is_the_opt_in_and_it_is_remembered(self):
        self.needs_the_pinned_sdk()
        self.consent(None)
        self.environment(**{config.ENV_KEY: "pk_test_dock", config.ENV_URL: UNREACHABLE_ENGINE})
        chat_dock.remove()
        self.dock = chat_dock.ensure()
        self.assertEqual(self.dock.status, config.CONSENT_NOTICE)
        self.dock.actionButton.click()
        self.assertIsNotNone(lifecycle.current())
        self.assertTrue(chat_dock.consented())
        self.dock.stop()
        self.assertEqual(self.dock.status, "Chat stopped.")  # the notice is said once

    def test_the_start_by_itself_never_installs_a_package(self):
        """Slicer's catalog: no pip at Slicer startup. A SlicerFlow update that moved the
        SDK waits for Start chat, which installs it."""
        self.consent(True)
        self.environment(**{config.ENV_KEY: "pk_test_dock", config.ENV_URL: UNREACHABLE_ENGINE})
        installs = []
        for name, fake in (("needs_install", lambda: True),
                           ("install", lambda *a, **k: installs.append("sdk"))):
            self.addCleanup(setattr, sdk, name, getattr(sdk, name))
            setattr(sdk, name, fake)
        self.dock.enable_autostart()
        _pump(0.5)
        self.assertIsNone(lifecycle.current())
        self.assertEqual(installs, [])
        self.assertEqual(self.dock.status, chat_dock.INSTALL_NOTICE)
        self.assertEqual(self.dock.actionButton.text, "Start chat")

    def test_the_start_by_itself_asks_for_the_mic_library_without_pip(self):
        self.needs_the_pinned_sdk()
        self.consent(True)
        self.environment(**{config.ENV_KEY: "pk_test_dock", config.ENV_URL: UNREACHABLE_ENGINE})
        from SlicerFlowLib import audio

        asked = []
        self.addCleanup(setattr, audio, "platform_allows", audio.platform_allows)
        audio.platform_allows = lambda: True
        self.addCleanup(setattr, sdk, "ensure_sounddevice", sdk.ensure_sounddevice)
        sdk.ensure_sounddevice = lambda **kwargs: asked.append(kwargs) or False
        self.dock.enable_autostart()
        wait_until(lambda: lifecycle.current() is not None, what="the chat to start by itself")
        self.assertEqual(asked, [{"install": False}])
        self.dock.stop()
        self.dock.actionButton.click()  # the user's Start chat may install it
        self.assertEqual(asked[-1], {"install": True})

    def test_the_notice_names_what_leaves_the_computer(self):
        notice = config.CONSENT_NOTICE
        for words in ("Talk2View's servers", "PyPI", "what you type", "screenshot",
                      "Nothing is sent until you press Start chat"):
            self.assertIn(words, notice)

    # ── The chat lock ──

    def test_a_chat_open_in_another_window_is_shown_here_without_a_dialog(self):
        self.needs_the_pinned_sdk()
        records = _records(self)
        dialogs = []
        for name in ("errorDisplay", "warningDisplay", "confirmYesNoDisplay", "infoDisplay"):
            original = getattr(slicer.util, name)
            setattr(slicer.util, name, lambda *a, _n=name, **k: dialogs.append(_n))
            self.addCleanup(setattr, slicer.util, name, original)
        other = lifecycle.ChatLock(paths.lock_file())  # what another Slicer's chat holds
        other.acquire()
        self.addCleanup(other.release)

        problem = self.dock.start(TEST_CONNECTION)

        self.assertIsNone(lifecycle.current())
        self.assertIn("another 3D Slicer window", problem)
        self.assertIn("another 3D Slicer window", self.dock.status)
        self.assertEqual(self.dock.actionButton.text, "Start chat")
        self.assertEqual(dialogs, [])
        self.assertEqual([r.getMessage() for r in records if r.exc_info], [])
        self.assertEqual([r.getMessage() for r in records if r.levelno >= logging.ERROR], [])
        other.release()
        self.dock.actionButton.click()  # the other window stopped its chat
        self.assertIsNotNone(lifecycle.current())

    def test_a_lock_left_on_another_computer_is_removed_from_the_panel(self):
        self.needs_the_pinned_sdk()
        path = _lock_file_holding("another-computer")
        self.addCleanup(lambda: os.path.exists(path) and lifecycle.current() is None
                        and os.remove(path))
        self.dock.start(TEST_CONNECTION)
        self.assertIsNone(lifecycle.current())
        self.assertIn("another-computer", self.dock.status)
        self.assertTrue(os.path.exists(path), "removed without asking")
        self.assertEqual(self.dock.actionButton.text, "Remove the lock and start")
        self.dock.actionButton.click()
        self.assertIsNotNone(lifecycle.current())

    # ── Guests ──

    def test_a_guest_starts_only_when_the_user_sends(self):
        """Guests are allowed, and the page starts one with the user's first message
        (``startAnonymous`` before the ``send``), never by itself: the page is loaded here
        and left alone, and no guest appears until the command the page sends."""
        self.needs_the_pinned_sdk()
        from SlicerFlowLib.runtime import Runtime

        self.assertTrue(Runtime._base_options(dictation=False).allow_anonymous)
        signed_out = lambda: os.path.exists(paths.token_file()) and os.remove(paths.token_file())  # noqa: E731
        signed_out()  # whoever an earlier test signed in
        self.addCleanup(signed_out)  # the guest, for the tests after this one
        engine = FakeEngine().start()
        self.addCleanup(engine.stop)
        self.dock.start(config.Connection("pk_test_guest", engine.url, "settings"))
        runtime = lifecycle.current()
        self.assertFalse(runtime.t2v.anonymous_auto_start)
        wait_until(lambda: runtime.server._hub.client_count > 0, timeout_s=30,
                   what="the page in the panel to connect")
        _pump(2.0)
        guests = [p for _m, p, _b in engine.requests if p == "/v1/auth/anonymous"]
        self.assertEqual(guests, [], "a guest was started without the user")
        self.assertFalse(runtime.t2v.auth.is_authenticated())

        token = runtime.server.url.split("#t=", 1)[1]
        request = urllib.request.Request(
            "http://127.0.0.1:{}/command".format(runtime.server.port),
            data=json.dumps({"id": str(uuid.uuid4()), "type": "startAnonymous"}).encode("utf-8"),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 202)
        # Both at once: the SDK stores the session on the thread that handled the command,
        # so a check of one right after waiting for the other races its writes.
        auth = runtime.t2v.auth
        wait_until(lambda: auth.is_authenticated() and auth.is_anonymous(),
                   what="the guest session")
