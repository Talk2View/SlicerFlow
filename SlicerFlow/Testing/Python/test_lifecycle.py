"""The chat runtime: one per process, stopped by Reload and by quitting, and never two on
one sign-in file."""

import json
import logging
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

import sf_testing  # noqa: F401  (isolates SLICERFLOW_HOME first)
import slicer

from fake_engine import FakeEngine
from sf_testing import UNREACHABLE_ENGINE, wait_until
from SlicerFlowLib import lifecycle, paths

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 3000


def _healthy(port):
    try:
        with urllib.request.urlopen("http://127.0.0.1:{}/healthz".format(port), timeout=2) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _library():
    return {n: m for n, m in sys.modules.items() if n == "SlicerFlowLib" or n.startswith("SlicerFlowLib.")}


def _restore(library):
    """Put back the library modules a Reload replaced. The other test files imported these
    objects when they were loaded, and a runtime must configure the same ones they call
    (``tools.capture``'s screenshot sink, for one)."""
    for name in _library():
        del sys.modules[name]
    sys.modules.update(library)


def _runtime(shown=None, base_url=UNREACHABLE_ENGINE, **kwargs):
    from SlicerFlowLib.runtime import Runtime

    show = shown.append if shown is not None else (lambda url: None)
    return Runtime("pk_test_lifecycle", base_url, show, **kwargs)


def _recording(name, close, closed):
    """``close``, with its name written down when it runs."""

    def record():
        closed.append(name)
        close()

    return record


def _lock_file_holding(test, hostname, pid="999999"):
    """A lock file like Qt's, in a directory of its own. The machine id is not this one, so
    Qt cannot check whether that process lives and refuses the lock, which is the only way
    to see what ``acquire()`` says about it."""
    path = os.path.join(tempfile.mkdtemp(), "chat.lock")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{}\nSlicer\n{}\n{}\n{}\n".format(pid, hostname, "0" * 32, "1" * 32))
    return path


def _slicerflow_logs(test):
    """What ``SlicerFlow`` logs from here on, whatever level Slicer left the logger at."""
    records = []

    class Recorder(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("SlicerFlow")
    handler = Recorder()
    logger.addHandler(handler)
    test.addCleanup(logger.removeHandler, handler)
    previous = logger.level
    logger.setLevel(logging.INFO)
    test.addCleanup(logger.setLevel, previous)
    return records


def _follow_up_threads():
    return [t for t in threading.enumerate() if t.name == "sf-follow-up" and t.is_alive()]


def _black_holed_engine(test):
    """An engine that accepts the connection and then never answers, which is what a
    firewall drop looks like to the SDK. ``UNREACHABLE_ENGINE`` is refused at once instead,
    so only this one shows whether Start chat waits for the engine."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)  # the backlog completes the handshake; nothing ever replies
    test.addCleanup(listener.close)
    return "http://127.0.0.1:{}".format(listener.getsockname()[1])


class LifecycleTest(unittest.TestCase):
    def tearDown(self):
        lifecycle.stop()

    def test_start_serves_the_chat_and_shows_its_url(self):
        shown = []
        url = lifecycle.start(_runtime(shown))
        self.assertTrue(url.startswith("http://127.0.0.1:"), url)
        self.assertIn("/#t=", url)
        self.assertTrue(_healthy(lifecycle.current().server.port))
        wait_until(lambda: shown, what="the host to show the chat URL")
        self.assertEqual(shown, [url])

    def test_a_second_start_stops_the_first(self):
        lifecycle.start(_runtime())
        first = lifecycle.current().server.port
        lifecycle.start(_runtime())
        self.assertFalse(_healthy(first))
        self.assertTrue(_healthy(lifecycle.current().server.port))

    def test_stop_is_idempotent(self):
        lifecycle.start(_runtime())
        port = lifecycle.current().server.port
        lifecycle.stop()
        lifecycle.stop()
        self.assertIsNone(lifecycle.current())
        self.assertFalse(_healthy(port))

    def test_a_failed_start_unwinds_and_frees_the_lock(self):
        broken = _runtime(static_dir=os.path.join(tempfile.mkdtemp(), "no-page-here"))
        with self.assertRaises(RuntimeError):  # talk2view.ui.StaticAssetsMissingError
            lifecycle.start(broken)
        self.assertIsNone(lifecycle.current())
        self.assertFalse(broken.running)
        # ``broken.lock`` keeps the QLockFile alive, so this really does check that the
        # runtime released it: an unreferenced one is collected, and Qt's destructor
        # unlocks it, which would let a lock nobody released pass this test.
        self.assertFalse(os.path.exists(paths.lock_file()), "the chat lock was not released")
        lifecycle.start(_runtime())  # the lock was released
        self.assertIsNotNone(lifecycle.current())

    def test_a_runtime_unwinds_its_own_failed_start(self):
        broken = _runtime(static_dir=os.path.join(tempfile.mkdtemp(), "no-page-here"))
        with self.assertRaises(RuntimeError):
            broken.start()  # not through lifecycle: a runtime undoes its own failed start
        self.assertFalse(broken.running)
        self.assertFalse(os.path.exists(paths.lock_file()), "the chat lock was not released")

    def test_the_chat_lock_refuses_a_second_holder_until_the_chat_stops(self):
        lifecycle.start(_runtime())
        other = lifecycle.ChatLock(paths.lock_file())
        with self.assertRaises(lifecycle.ChatAlreadyRunningError):
            other.acquire()
        lifecycle.stop()
        other.acquire()
        other.release()

    def test_the_refusal_names_the_holder_and_the_lock_file(self):
        lifecycle.start(_runtime())
        other = lifecycle.ChatLock(paths.lock_file())
        with self.assertRaises(lifecycle.ChatAlreadyRunningError) as caught:
            other.acquire()
        message = str(caught.exception)
        self.assertIn(paths.lock_file(), message)
        self.assertIn(str(os.getpid()), message)  # this process holds it
        self.assertEqual(caught.exception.holder[0], str(os.getpid()))
        # A live holder on this computer is never offered for removal, and Qt refuses it.
        self.assertFalse(caught.exception.removable)
        self.assertFalse(other.remove_stale_lock())
        self.assertTrue(os.path.exists(paths.lock_file()))

    def test_a_lock_left_on_another_computer_says_so_and_can_be_removed(self):
        path = _lock_file_holding(self, "another-computer")
        lock = lifecycle.ChatLock(path)
        with self.assertRaises(lifecycle.ChatAlreadyRunningError) as caught:
            lock.acquire()
        message = str(caught.exception)
        self.assertIn("another-computer", message)
        self.assertIn(path, message)
        # Qt cannot ask another computer whether that process still runs, so only the user
        # can clear this one.
        self.assertTrue(caught.exception.removable)
        self.assertTrue(lock.remove_stale_lock())
        lock.acquire()
        lock.release()

    def test_a_lock_with_no_hostname_is_treated_as_this_computer(self):
        # Qt writes an empty host when it cannot name one, and reads that back as local.
        # "on another computer, " with a blank name would be a lie, and offering to remove
        # it would invite the user to clear a lock this very computer may hold.
        for hostname in ("", "   "):
            with self.subTest(hostname=repr(hostname)):
                lock = lifecycle.ChatLock(_lock_file_holding(self, hostname))
                with self.assertRaises(lifecycle.ChatAlreadyRunningError) as caught:
                    lock.acquire()
                message = str(caught.exception)
                self.assertNotIn("another computer", message)
                self.assertIn("999999", message)
                self.assertFalse(caught.exception.removable)

    def test_a_client_closed_mid_flight_is_logged_quietly(self):
        # Stop chat closes the client while the registration POST may still be in flight.
        # httpx raises a plain RuntimeError for that, which is not a T2VError, and an
        # ordinary stop must not look like a crash in the log.
        records = _slicerflow_logs(self)
        runtime = _runtime()
        lifecycle.start(runtime)
        runtime.t2v.close()  # what stop() does
        runtime._send_schemas()  # the thread's work, run here so nothing depends on timing
        self.assertTrue(records, "the closed client was not logged at all")
        self.assertEqual([r.getMessage() for r in records if r.levelno >= logging.ERROR], [])
        traced = [r.getMessage() for r in records if r.exc_info]
        self.assertEqual(traced, [], "a normal Stop chat printed a stack trace")

    @unittest.skipIf(sys.platform == "win32", "Windows ignores a folder's read-only mode")
    def test_a_lock_file_it_cannot_create_says_that_instead(self):
        directory = os.path.join(tempfile.mkdtemp(), "read-only")
        lock = lifecycle.ChatLock(os.path.join(directory, "chat.lock"))  # makes the directory
        os.chmod(directory, 0o500)
        self.addCleanup(os.chmod, directory, 0o700)
        with self.assertRaises(lifecycle.ChatLockError) as caught:
            lock.acquire()
        # Not contention: saying another window has the chat open would send the user
        # looking for a Slicer that does not exist.
        self.assertNotIsInstance(caught.exception, lifecycle.ChatAlreadyRunningError)
        self.assertIn(directory, str(caught.exception))
        self.assertIn("permission", str(caught.exception).lower())

    def test_starting_does_not_wait_for_the_engine(self):
        started = time.monotonic()
        lifecycle.start(_runtime(base_url=_black_holed_engine(self)))
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 5.0, "Start chat waited {:.1f} s for the engine".format(elapsed))
        # The handlers are the half that needs no network, so they are ready right away.
        self.assertTrue(lifecycle.current().t2v.tools.has_handler("get_scene_context"))

    def test_stop_does_not_leave_a_thread_parked_in_a_screenshot_upload(self):
        """Closing the SDK's client does not interrupt a request another thread is already
        reading: that thread stays in the socket read until the upload times out, holding
        the screenshot, the closed client and the whole stopped runtime, and one more piles
        up per Stop pressed during an upload. A bounded upload timeout is what lets it go.

        A short one is used here so the test does not have to sit out the shipped one; what
        it pins is that the wait ends by itself, and ``test_the_upload_timeout_is_bounded``
        pins that the shipped one is short enough to matter.
        """
        engine = FakeEngine().start()
        self.addCleanup(engine.stop)
        engine.hold_uploads()
        self.addCleanup(engine.release_uploads)
        runtime = _runtime(base_url=engine.url, upload_timeout=1.0)
        lifecycle.start(runtime)
        runtime.t2v.auth.login("user@example.com", "secret")
        runtime.follow_ups.add(PNG, "3D view")
        wait_until(lambda: engine.upload_started.is_set(), what="the screenshot upload to start")
        # It must never hold up quitting Slicer either, however long it is stuck for.
        self.assertTrue(all(t.daemon for t in _follow_up_threads()), "not a daemon thread")

        lifecycle.stop()  # Stop chat, with the PNG on the wire

        wait_until(lambda: not _follow_up_threads(), timeout_s=15,
                   what="the follow-up thread to leave the upload")

    def test_the_upload_timeout_is_bounded(self):
        from SlicerFlowLib.runtime import UPLOAD_TIMEOUT_S

        runtime = _runtime()
        lifecycle.start(runtime)
        # The SDK's facade does not re-export the timeout, so this reads the client's own
        # documented property: what matters is that the value reached the thing that waits.
        self.assertEqual(runtime.t2v._client.upload_timeout, UPLOAD_TIMEOUT_S)
        # The SDK's own default is 120 s: two minutes of a stopped chat kept alive.
        self.assertLessEqual(UPLOAD_TIMEOUT_S, 60.0)

    def test_the_main_thread_queue_is_drained_every_20_ms(self):
        """The timer is the only way a worker thread's work reaches Slicer's main
        thread, so its interval is the floor under every tool call and posted callback."""
        runtime = _runtime()
        lifecycle.start(runtime)
        self.assertEqual(runtime.host._timer.interval, 20)

    def test_guests_are_allowed_and_never_started_by_the_runtime(self):
        """The partner decides about guests (the engine enforces its limits, and a refused
        guest reaches the page as the sign-in form with the reason). A guest starts with
        the user's first message only: never at start, never from a warm-up."""
        from SlicerFlowLib.runtime import Runtime

        self.assertTrue(Runtime._base_options(dictation=False).allow_anonymous)
        # The Skills view heads the app's skills "From SlicerFlow".
        self.assertEqual(Runtime._base_options(dictation=False).app_name, "SlicerFlow")
        runtime = _runtime()
        lifecycle.start(runtime)
        self.assertFalse(runtime.t2v.anonymous_auto_start)

    def test_welcome_carries_a_subheading(self):
        from SlicerFlowLib.runtime import WELCOME

        self.assertEqual(WELCOME.heading, "Ask about your Slicer scene")
        self.assertEqual(
            WELCOME.subheading,
            "Load data, change views and run tools by talking to Slicer.",
        )

    # ── A guest session is kept, like an account's ──

    def _stored(self):
        """The sign-in file as it is on disk, read directly rather than through a live
        ``FileStorage`` -- this helper is also used after ``lifecycle.on_quit()``, when
        no runtime (and so no storage instance) exists to ask."""
        try:
            with open(paths.token_file(), encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return {}

    def _store_session(self, anonymous, access_token="access-stored"):
        """Write a session into the file as an earlier Slicer process would."""
        os.makedirs(os.path.dirname(paths.token_file()), exist_ok=True)
        with open(paths.token_file(), "w", encoding="utf-8") as fh:
            json.dump({"access_token": access_token, "refresh_token": "refresh-stored",
                       "user": '{"id": "stored", "email": ""}',
                       "is_anonymous": "true" if anonymous else "false"}, fh)

    def _signed_out_after(self):
        self.addCleanup(lambda: os.path.exists(paths.token_file())
                        and os.remove(paths.token_file()))

    def test_a_guest_session_is_kept_when_slicer_quits(self):
        """The next launch carries on as the same guest, as the website does."""
        self._signed_out_after()
        engine = FakeEngine().start()
        self.addCleanup(engine.stop)
        runtime = _runtime(base_url=engine.url)
        lifecycle.start(runtime)
        runtime.t2v.auth.start_anonymous()
        stored = self._stored()
        self.assertEqual(stored.get("is_anonymous"), "true")
        lifecycle.on_quit()
        self.assertIsNone(lifecycle.current())
        self.assertEqual(self._stored().get("access_token"), stored.get("access_token"))
        self.assertEqual(self._stored().get("refresh_token"), stored.get("refresh_token"))

    def test_an_account_stays_signed_in_when_slicer_quits(self):
        self._signed_out_after()
        engine = FakeEngine().start()
        self.addCleanup(engine.stop)
        runtime = _runtime(base_url=engine.url)
        lifecycle.start(runtime)
        runtime.t2v.auth.login("user@example.com", "secret")
        lifecycle.on_quit()
        self.assertIsNotNone(self._stored().get("access_token"))
        self.assertEqual(self._stored().get("is_anonymous"), "false")

    def test_a_new_process_carries_on_as_the_stored_guest(self):
        self._signed_out_after()
        self._store_session(anonymous=True)
        runtime = _runtime()
        lifecycle.start(runtime)
        self.assertEqual(self._stored().get("access_token"), "access-stored")
        self.assertTrue(runtime.t2v.auth.is_anonymous())

    def test_a_new_process_keeps_an_account(self):
        self._signed_out_after()
        self._store_session(anonymous=False)
        lifecycle.start(_runtime())
        self.assertEqual(self._stored().get("access_token"), "access-stored")

    def test_the_tools_that_ask_are_ready_without_the_engine_being_told(self):
        """The handlers and their permissions are local, so a tool call must be gated the
        moment Start chat returns — before, and even without, the POST that tells the
        engine, which runs on a thread of its own and may never succeed. That POST is
        stubbed out here, so what is left is only what Start chat itself did.
        """
        from talk2view import Talk2View

        t2v = Talk2View("pk_test_register", base_url=UNREACHABLE_ENGINE)
        self.addCleanup(t2v.close)
        runtime = _runtime()
        runtime.t2v = t2v
        runtime._send_schemas = lambda: None
        runtime._register_tools()
        for name in ("load_sample_data", "load_file", "threshold_segment", "capture_view"):
            self.assertEqual(
                t2v.tools.check_permission(name, {}).action, "require_approval", name)
        self.assertEqual(t2v.tools.check_permission("get_scene_context", {}).action, "allow")

    def test_closing_the_host_stops_its_main_thread_timer(self):
        runtime = _runtime()
        lifecycle.start(runtime)
        timer = runtime.host._timer
        self.assertTrue(timer.isActive())
        lifecycle.stop()
        self.assertFalse(timer.isActive())

    def test_stop_closes_the_newest_part_first(self):
        runtime = _runtime()
        lifecycle.start(runtime)
        # The order start() builds them in.
        parts = ["lock", "host", "client", "sign-in listener", "timings", "controller",
                 "follow-ups", "server"]
        self.assertEqual(len(runtime._closers), len(parts))
        closed = []
        runtime._closers[:] = [
            _recording(name, close, closed) for name, close in zip(parts, runtime._closers)
        ]
        lifecycle.stop()
        self.assertEqual(
            closed, ["server", "follow-ups", "controller", "timings", "sign-in listener",
                     "client", "host", "lock"]
        )

    def test_the_ui_server_is_wired_for_the_skills_view_and_the_import_picker(self):
        """The SDK keeps the user's skills folder and the bundled ones (Task 10): no
        ``LocalSkills`` here any more, so this is wired straight into ``UIServer``. A stub
        in place of the real ``UIServer`` captures the keywords ``_start`` built it with."""
        from SlicerFlowLib import runtime as runtime_module
        from SlicerFlowLib import skills

        captured = {}

        class _StubServer:
            def __init__(self, *args, **kwargs):
                captured.update(kwargs)

            def start(self):
                return "http://127.0.0.1:0/#t=stub"

            def show(self):
                pass

            def stop(self):
                pass

        original = runtime_module.UIServer
        runtime_module.UIServer = _StubServer
        self.addCleanup(setattr, runtime_module, "UIServer", original)
        lifecycle.start(_runtime())
        self.assertEqual(captured["skills_dir"], paths.user_skills_dir())
        self.assertEqual(captured["bundled_skill_dirs"], [skills.BUNDLED_DIR])
        self.assertIs(captured["on_pick_skill_import"], skills.pick_import)
        self.assertNotIn("on_skills_list", captured)

    def test_quitting_stops_the_chat_and_the_hook_is_connected_once(self):
        lifecycle.install_quit_hook()
        lifecycle.install_quit_hook()
        self.assertTrue(getattr(slicer.modules, lifecycle.QUIT_HOOK_ATTR))
        lifecycle.start(_runtime())
        port = lifecycle.current().server.port
        lifecycle.on_quit()
        self.assertIsNone(lifecycle.current())
        self.assertFalse(_healthy(port))

    def test_the_quit_hook_is_connected_exactly_once(self):
        # Counted, not taken on trust: the flag the code sets says nothing about how many
        # connections Qt holds. The prototype connected one per setup(), so every Reload
        # added another aboutToQuit call.
        lifecycle.install_quit_hook()
        lifecycle.install_quit_hook()
        connections = 0
        while slicer.app.disconnect("aboutToQuit()", lifecycle.on_quit):
            connections += 1
        self.addCleanup(slicer.app.connect, "aboutToQuit()", lifecycle.on_quit)
        self.assertEqual(connections, 1)

    def test_reload_stops_the_chat_and_reimports_the_library(self):
        self.addCleanup(_restore, _library())
        widget = slicer.modules.slicerflow.widgetRepresentation().self()
        lifecycle.start(_runtime())
        port = lifecycle.current().server.port
        before = sys.modules["SlicerFlowLib.lifecycle"]
        dock_before = getattr(slicer.modules, "slicerflowChatDock")
        self.assertIsNotNone(dock_before)
        widget.onReload()
        self.assertFalse(_healthy(port))
        self.assertIsNone(getattr(slicer.modules, lifecycle.RUNTIME_ATTR))
        # The reloaded SlicerFlow.py imported the library again, from its current code.
        self.assertIsNot(sys.modules["SlicerFlowLib.lifecycle"], before)
        # ...and built the chat panel again from it: one panel, one View menu entry.
        dock_after = getattr(slicer.modules, "slicerflowChatDock")
        self.assertIsNotNone(dock_after)
        self.assertIsNot(dock_after, dock_before)
        window = slicer.util.mainWindow()
        self.assertEqual(len(slicer.util.findChildren(window, name="SlicerFlowChatDock")), 1)
        menu = slicer.util.findChildren(window, name="ViewMenu")[0]
        self.assertEqual([a.text for a in menu.actions()].count("SlicerFlow by Talk2View"), 1)

    def test_the_sign_in_and_lock_files_live_in_slicerflow_home(self):
        home = os.environ["SLICERFLOW_HOME"]
        self.assertEqual(paths.token_file(), os.path.join(home, "auth.json"))
        self.assertEqual(paths.lock_file(), os.path.join(home, "chat.lock"))
