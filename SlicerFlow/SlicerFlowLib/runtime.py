"""One running Talk2View chat inside Slicer: the chat lock, the host adapter, the SDK
client with the Slicer tools and the user's tool packs (``user_tools``), the turn timings
(``perf``), the chat controller, the screenshot follow-ups and the localhost UI server the
chat panel's web view loads. The UI server keeps the bundled and the user's skills (``skills_dir``, ``bundled_skill_dirs``);
nothing here loads them itself.

Build and stop it on the main thread, through ``lifecycle``. ``start()`` builds the parts in
order and ``stop()`` closes whatever was built, newest first: the UI server, the screenshot
follow-ups (the sink is cleared), the controller, the turn timings (a turn still waiting for
the page's report is written without it), the client, the host's timer, the lock.
``stop()`` is idempotent, and a ``start()`` that fails part-way undoes itself before it
raises.
"""

import logging
import threading

from talk2view import NetworkError, T2VError, Talk2View
from talk2view.ui import ChatFeatures, ChatOptions, ChatWelcome, ToolPresentation, UIServer

from . import paths, perf, user_tools
from .followup import FollowUpController, FollowUps
from .host import SlicerHost
from .lifecycle import ChatLock
from .prompt import system_prompt
from .skills import BUNDLED_DIR, ensure_user_dir, pick_import
from .tools import MODULES, NEVER, PRESENTATION, TOOLS, capture

WELCOME = ChatWelcome(
    heading="Ask about your Slicer scene",
    subheading="Load data, change views and run tools by talking to Slicer.",
    suggestions=[
        "Load the MRHead sample and show it in 3D",
        "What is loaded in my scene?",
        "Switch to the four-up layout and center the views",
    ],
)

#: The Talk2View website's page for a forgotten password (the page's "Forgot it?" link).
RESET_PASSWORD_URL = "https://talk2view.com/reset-password"

#: Seconds a screenshot upload may take. Closing the SDK's client does not interrupt a
#: request another thread is already reading, and ``Runtime.stop()`` never joins the
#: delivery thread, so this is how long a Stop pressed mid-upload leaves that thread parked
#: in a socket read — holding the screenshot, the closed client and the whole stopped
#: runtime, one thread per Stop. The SDK's own default is 120 s, which is two minutes of
#: that; a screenshot is small, and the same 30 s an ordinary request gets is plenty.
#: (Interrupting the upload itself needs a cancellable one in the Python SDK.)
UPLOAD_TIMEOUT_S = 30.0

logger = logging.getLogger("SlicerFlow")


class Runtime:
    """``Runtime(...)`` only stores its arguments; ``start()`` builds everything and returns
    the chat URL, which the host then shows through ``show_url``. ``static_dir`` None serves
    the chat page inside the Talk2View SDK.

    ``on_unreachable(error)`` and ``on_reachable()``, when given, are called on the main
    thread: the first when the tool registration (at start, or ``check_engine()``) could not
    reach the engine (``NetworkError``) or failed some other way, the second when the engine answered it (even with a
    refusal) or someone signed in. Neither is called after ``stop()``."""

    def __init__(self, partner_key, base_url, show_url, *, static_dir=None,
                 upload_timeout=UPLOAD_TIMEOUT_S, on_unreachable=None, on_reachable=None,
                 install_packages=True):
        self._partner_key = partner_key
        #: False when the chat starts by itself with Slicer: nothing is pip-installed then.
        self._install_packages = install_packages
        self._base_url = base_url
        self._show_url = show_url
        self._static_dir = static_dir
        self._upload_timeout = upload_timeout
        self._on_unreachable = on_unreachable
        self._on_reachable = on_reachable
        self._signed_in_off = None
        self._closers = []
        self.lock = None
        self.host = None
        self.t2v = None
        self.controller = None
        self.follow_ups = None
        self._auth_off = None
        self.server = None
        self.performance = None
        self._timing_off = None
        self.packs = []
        self.tools = list(TOOLS)
        self.presentation = dict(PRESENTATION)
        self.prompt = system_prompt(getattr(fn, "name", fn.__name__) for fn in TOOLS)

    def start(self):
        """Build everything and return the chat URL. A failure part-way undoes what was
        built and raises: no lock, server or client is left behind."""
        try:
            return self._start()
        except BaseException:
            self.stop()
            raise

    def _start(self):
        lock = ChatLock(paths.lock_file())
        lock.acquire()
        self.lock = lock
        self._closers.append(lock.release)
        self.host = SlicerHost(self._show_url, capture=self._capture(self._install_packages))
        self._closers.append(self.host.close)
        self.t2v = Talk2View(
            self._partner_key,
            base_url=self._base_url,
            storage=self.host.storage(),
            # The chat list, kept out of auth.json (rewritten on every refresh): its own
            # file, conversations.json (paths.conversations_file(), host.py).
            data_storage=self.host.data_storage(),
            dispatcher=self.host,
            upload_timeout=self._upload_timeout,
            # A guest starts only when the signed-out user sends their first message (the
            # page's startAnonymous), never at start and never from a warm-up.
            anonymous_auto_start=False,
        )
        self._closers.append(self.t2v.close)
        self._signed_in_off = self.t2v.auth.on_auth_state_change(self._on_account_change)
        self._closers.append(self._stop_listening_for_sign_in)
        self._load_packs()
        self._register_tools()
        # Every message's timings: metrics.jsonl, and the module panel's Performance section.
        self.performance = perf.Performance(perf.MetricsLog(paths.metrics_file()), self.host.pump)
        self._timing_off = self.t2v.metrics.on_turn(self.performance.on_turn)
        self._closers.append(self._stop_timing)
        user_skills_dir = ensure_user_dir()
        # Every tool that asks, asks every time: the controller answers an "always" as
        # "once", whoever sent it and whatever card is up (``FollowUpController.approve``).
        self.prompt = system_prompt(getattr(fn, "name", fn.__name__) for fn in self.tools)
        self.controller = FollowUpController(self.t2v, system_prompt=self.prompt)
        self._closers.append(self.controller.close)
        self.follow_ups = FollowUps(self.controller, self._upload_png, self._user_id)
        self.controller.add_stop_listener(self.follow_ups.drop)
        # Stop and New chat come through the controller; a sign-out or a sign-in does not,
        # so the queue listens to the account itself as well.
        self._auth_off = self.t2v.auth.on_auth_state_change(self.follow_ups.on_account_change)
        capture.set_sink(self.follow_ups.add)
        self._closers.append(self._close_follow_ups)
        self.server = UIServer(
            self.controller,
            self.host,
            static_dir=self._static_dir,
            options=self._options(),
            # The SDK keeps the user's skills folder (the Skills view writes, edits,
            # imports and deletes there) and layers the bundled ones read-only under it;
            # left unset, on_skills_list defaults to that store's own rescan.
            skills_dir=user_skills_dir,
            bundled_skill_dirs=[BUNDLED_DIR],
            on_pick_skill_import=pick_import,
        )
        self._closers.append(self.server.stop)
        url = self.server.start()
        self.server.show()  # posted: the host opens it on the next main-thread tick
        return url

    @staticmethod
    def _capture(install=True):
        """The microphone, when it can work here (``audio.available()``), else None. The
        install and the device query take a moment on Slicer's main thread, once per start.
        Where the platform rules the mic out (macOS without the plist key), nothing is
        installed: pip would only open its progress window for a button that never shows.
        With ``install`` False (a start by itself with Slicer) nothing is installed either:
        the mic needs ``sounddevice`` already there, or a Start chat that installs it."""
        from . import audio, sdk

        if not audio.platform_allows():
            logger.info("SlicerFlow: this Slicer declares no microphone use; no dictation")
            return None
        if not sdk.ensure_sounddevice(install=install):
            logger.info("SlicerFlow: sounddevice is not available; no dictation")
            return None
        capture = audio.make_capture()
        if capture is None:
            logger.info("SlicerFlow: no usable microphone here; no dictation")
        return capture

    @staticmethod
    def _base_options(*, dictation, presentation=PRESENTATION):
        """The parts of ``ChatOptions`` that never depend on a running host: ``_options()``
        adds ``features.dictation`` and the loaded packs' ``presentation``. Tests that want a default ``ChatOptions`` without
        building a ``Runtime`` (``allow_anonymous``, ``tools``) call this directly."""
        return ChatOptions(
            welcome=WELCOME,
            # A file picker is not in the web view yet.
            features=ChatFeatures(dictation=dictation, attachments=False),
            # Guests are the partner's call: the engine enforces its guest limits. When it
            # refuses a guest (a captcha the desktop cannot solve, guests off, the day's cap)
            # the page shows the sign-in form with a generic "Guest chat isn't available
            # right now" that does not say which; a spent allowance gets "You've reached
            # the guest limit". A guest session is kept across launches, like an account's.
            allow_anonymous=True,
            reset_password_url=RESET_PASSWORD_URL,
            footer="Answers can be wrong. Check them before you act on them.",
            # The Skills view heads the bundled skills "From SlicerFlow".
            app_name="SlicerFlow",
            tools={name: ToolPresentation(**spec) for name, spec in presentation.items()},
        )

    def _load_packs(self):
        """The packs that are on, loaded now. First SlicerFlow's own packs are brought up to
        date in the user's tools folder (``user_tools.sync_builtins``). The unedited ones load
        from the extension's modules, in ``BUILTIN_PACKS`` order, unless they are in
        ``builtin_off``. An edited one is the user's code: it loads from its file, like a user
        pack (its code runs now, and a tool that does not say ``permission=`` asks first),
        when its stem is in ``enabled``, and before the user's packs so it keeps its tool
        names. Then the user's packs (``enabled``). A pack that fails is kept, with its error,
        for the module panel, and adds nothing; the others load regardless. Every unedited
        built-in tool name, on or off, and the names SlicerFlow never uses (``NEVER``) are
        taken: a user pack cannot take one over."""
        state = user_tools.read_state(paths.user_tools_file())
        folder = user_tools.ensure_user_dir(paths.user_tools_dir())
        files, changed = user_tools.sync_builtins(
            folder, state, user_tools.builtin_sources(user_tools.BUILTIN_DIR))
        if changed:
            user_tools.save_state(paths.user_tools_file(), state)
        taken = {name: "SlicerFlow" for name in NEVER}
        packs = []
        for item in files:  # a name in builtin_off that is no pack is ignored
            if item.edited or item.name in state.builtin_off:
                continue
            pack = user_tools.builtin_pack(item.name, MODULES[item.name], taken, path=item.path)
            taken.update((tool.name, "SlicerFlow") for tool in pack.tools)
            packs.append(pack)
        reserved = dict(taken)
        for item in files:
            if not item.edited:
                reserved.update((tool.name, "SlicerFlow") for tool in MODULES[item.name].TOOLS)
        for item in files:
            if item.edited and item.stem in state.enabled:
                pack = user_tools.load_pack(item.stem, item.path, reserved)
                reserved.update((tool.name, "the tool pack " + item.stem) for tool in pack.tools)
                packs.append(pack)
        user = user_tools.load_packs(folder, state.enabled, reserved,
                                     skip={item.stem for item in files})
        self.packs = packs + user
        self.tools = []
        self.presentation = {}
        for pack in self.packs:
            if pack.error:
                logger.warning("SlicerFlow: the tool pack %s did not load: %s", pack.title,
                               pack.error)
            elif pack.missing:
                logger.info("SlicerFlow: the tool pack %s needs %s", pack.title,
                            ", ".join(sorted(pack.missing.values())))
            self.tools.extend(pack.tools)
            self.presentation.update(pack.presentation)

    def _options(self):
        return self._base_options(dictation=self.host.audio() is not None,
                                  presentation=self.presentation)

    def _register_tools(self):
        """Register the handlers here and tell the engine on a thread.

        Handlers and their permissions are local, so they are ready the moment Start chat
        returns. Sending the schemas is a POST, and an engine that accepts the connection
        and then never answers would freeze Slicer's main thread until the SDK's timeout,
        so that half runs on a short thread of its own.
        """
        for fn in self.tools:
            self.t2v.tools.handle(
                getattr(fn, "name", fn.__name__),
                getattr(fn, "_t2v_handler", fn),
                getattr(fn, "_t2v_permission", None),
            )
        self.check_engine()

    def check_engine(self):
        """Send the tool schemas again on a thread of its own: its answer is the one sign
        that the engine can (``on_reachable``) or cannot (``on_unreachable``) be reached.
        Nothing about the running chat changes."""
        threading.Thread(
            target=self._send_schemas, name="sf-register-tools", daemon=True
        ).start()

    def _post(self, callback, *args):
        # Through the host's queue: a stopped runtime's queue never runs again. The
        # arguments are bound now: Python unbinds an ``except`` block's name when it ends.
        if callback is not None and self.host is not None:
            self.host.post(lambda: callback(*args))

    def _on_account_change(self, user):
        if user is not None:  # a sign-in or a guest start: the engine answered
            self._post(self._on_reachable)

    def _stop_timing(self):
        # A turn still waiting for the chat page's report is written now, without it.
        self.t2v.metrics.close()
        if self._timing_off is not None:
            self._timing_off()
            self._timing_off = None

    def _stop_listening_for_sign_in(self):
        if self._signed_in_off is not None:
            self._signed_in_off()
            self._signed_in_off = None

    def _send_schemas(self):
        # register_from_functions stores the schemas before it POSTs them, and the SDK
        # re-sends what it stored whenever a session starts. So the tools still reach the
        # engine when this POST fails because nobody has signed in yet.
        try:
            self.t2v.tools.register_from_functions(self.tools)
        except NetworkError as exc:
            logger.info("SlicerFlow: the engine could not be reached (%s)", exc)
            self._post(self._on_unreachable, exc)
        except T2VError as exc:
            logger.info("SlicerFlow: tools will register after sign-in (%s)", exc.type)
            self._post(self._on_reachable)  # it answered, if only to refuse
        except Exception as exc:
            # Stopping the chat closes the client under this POST, and httpx raises a plain
            # RuntimeError for that, which is not a T2VError. An ordinary Stop chat must not
            # leave a stack trace in the log, so this stays a note.
            logger.info("SlicerFlow: the tools did not reach the engine (%s)", exc)
            self._post(self._on_unreachable, exc)  # a stopped runtime's post never runs
        else:
            self._post(self._on_reachable)

    def _upload_png(self, png, filename):
        return self.t2v.upload_attachment(png, filename=filename, mime_type="image/png")

    def _user_id(self):
        user = self.t2v.auth.get_user()
        return user.id if user is not None else None

    def _close_follow_ups(self):
        capture.set_sink(None)
        if self._auth_off is not None:
            self._auth_off()
            self._auth_off = None
        self.follow_ups.close()

    @property
    def running(self):
        return bool(self._closers)

    def stop(self):
        while self._closers:
            close = self._closers.pop()
            try:
                close()
            except Exception:  # keep closing the rest
                logger.exception("SlicerFlow: stopping the chat failed")
