"""The chat panel: a dock titled "SlicerFlow by Talk2View" on the right of Slicer's main
window.

It holds the web view the chat page loads in, and a status line for what the page cannot
show: the SDK being installed, a chat open in another 3D Slicer window, an engine that
could not be reached, or why Start failed, each with the one button that helps. The
module panel on the left keeps the settings and a Start/Stop button that drives this;
starting the chat there shows this dock too (``start()`` calls ``show()`` first).

There is one per process, kept on ``slicer.modules`` (as ``slicerflowChatDock``) for the
same reason ``lifecycle`` keeps the runtime there: ``sdk.install()`` and the module's
Reload re-import this file, and the dock must outlive that. Reload tears it down
(``remove()``) and the reloaded module builds a fresh one (``ensure()``), so the dock
always runs the current code and there is never a second one.

Until the user has started the chat once, the status line says what the chat sends
(``config.CONSENT_NOTICE``) and nothing starts by itself: no SDK install, no request to
the engine. Pressing Start chat is the opt-in, remembered in Slicer's settings
(``config.SETTINGS_CONSENTED``); from then on the chat starts by itself with Slicer.

Closing the dock hides it and leaves the chat running; the View menu's toggle (or
starting the chat again) shows it again. Whether it was shown is saved in Slicer's
settings and restored at the next start.

Everything here runs on Slicer's main thread.
"""

import logging
import os

import qt
import slicer

from . import config, lifecycle, sdk

DOCK_ATTR = "slicerflowChatDock"
OBJECT_NAME = "SlicerFlowChatDock"
TITLE = "SlicerFlow by Talk2View"
PARTNER_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Resources", "partner.json"
)
#: The dock's width the first time it is shown.
INITIAL_WIDTH = 420
#: The narrowest the dock goes. Slicer's conventional layout needs 929 px for its three
#: slice views, and the module panel up to 401 px, so on a 1728 px laptop screen every
#: pixel here is one the module panel cannot have. The chat page lays out down to 260 px.
MIN_WIDTH = 260

logger = logging.getLogger("SlicerFlow")


#: The status line of a stopped chat that has been started before.
STOPPED = "Chat stopped."
#: Instead of starting by itself when the pinned SDK is not installed (a SlicerFlow update
#: moved it): Slicer's startup never runs pip, the user's Start chat does.
INSTALL_NOTICE = ("This version of SlicerFlow needs the Talk2View SDK {} from PyPI. Press "
                  "Start chat to install it and start the chat.").format(sdk.SDK_VERSION)


def consented():
    """Whether the user has started the chat before (``config.SETTINGS_CONSENTED``)."""
    return config.setting_is_true(qt.QSettings().value(config.SETTINGS_CONSENTED), False)


def current():
    """The chat dock, or None before one is built."""
    return getattr(slicer.modules, DOCK_ATTR, None)


def ensure():
    """The chat dock, built now if there is none yet; None in a Slicer without a main window
    (``--no-main-window``), which has nowhere to put it."""
    dock = current()
    if dock is None:
        if slicer.util.mainWindow() is None:
            return None
        dock = ChatDock()
        setattr(slicer.modules, DOCK_ATTR, dock)
    return dock


def remove():
    """Take the chat dock out of the main window. The chat itself is left alone: stop it
    first (``lifecycle.stop()``), since its web view goes with the dock."""
    dock = current()
    setattr(slicer.modules, DOCK_ATTR, None)
    if dock is not None:
        dock.destroy()


def saved_connection():
    """The partner key and engine from the environment or the extension's own file
    (``config.resolve``). There is no panel UI for either any more, so a saved override
    (an older build's Advanced section) is never read."""
    return config.resolve(os.environ, config.load_bundled(PARTNER_FILE))


def _view_menu(window):
    for menu in slicer.util.findChildren(window, name="ViewMenu"):
        return menu
    for action in window.menuBar().actions():
        if action.menu() is not None and action.text.replace("&", "") == "View":
            return action.menu()
    return None


class ChatDock:
    """The dock, its web view and status line, and starting and stopping the chat in it."""

    def __init__(self):
        window = slicer.util.mainWindow()
        self.window = window
        self.dock = qt.QDockWidget(TITLE, window)
        self.dock.objectName = OBJECT_NAME
        self.dock.setAllowedAreas(qt.Qt.LeftDockWidgetArea | qt.Qt.RightDockWidgetArea)

        body = qt.QWidget()
        body.setMinimumWidth(MIN_WIDTH)
        layout = qt.QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.statusRow = qt.QWidget()
        row = qt.QHBoxLayout(self.statusRow)
        row.setContentsMargins(8, 6, 8, 6)
        texts = qt.QVBoxLayout()
        texts.setSpacing(4)
        self.statusLabel = qt.QLabel()
        self.statusLabel.wordWrap = True
        # Plain text: an error's "<...>" must show as it is, never as markup.
        self.statusLabel.textFormat = qt.Qt.PlainText
        self.statusLabel.setTextInteractionFlags(qt.Qt.TextSelectableByMouse)
        texts.addWidget(self.statusLabel)
        # Under the notice only (``_show_stopped``): where the privacy policy is.
        self.privacyLink = qt.QLabel('<a href="{0}">Talk2View privacy policy</a>'.format(
            config.PRIVACY_URL))
        self.privacyLink.textFormat = qt.Qt.RichText
        self.privacyLink.openExternalLinks = True
        self.privacyLink.setVisible(False)
        texts.addWidget(self.privacyLink)
        self.actionButton = qt.QPushButton()
        row.addLayout(texts, 1)
        row.addWidget(self.actionButton, 0, qt.Qt.AlignTop)
        layout.addWidget(self.statusRow)
        self.web = slicer.qSlicerWebWidget()
        try:
            self.web.javaScriptConsoleMessageLoggingEnabled = True  # page console -> Slicer log
        except Exception:
            pass
        layout.addWidget(self.web, 1)
        self.dock.setWidget(body)
        self._body = body

        self._action = None
        self._kind = None
        self._listeners = []
        self._autostart_pending = False
        #: What the running chat was started with, or None.
        self.connection = None
        self._show_stopped()
        self.actionButton.connect("clicked(bool)", self._on_action)

        window.addDockWidget(qt.Qt.RightDockWidgetArea, self.dock)
        try:
            window.resizeDocks([self.dock], [INITIAL_WIDTH], qt.Qt.Horizontal)
        except Exception:  # only the first width: the dock works without it
            pass
        self.toggleAction = self.dock.toggleViewAction()
        self.menu = _view_menu(window)
        if self.menu is not None:
            self.menu.addAction(self.toggleAction)
        shown = config.setting_is_true(qt.QSettings().value(config.SETTINGS_PANEL_SHOWN), True)
        self.dock.setVisible(shown)
        # toggled follows the dock's own visibility (its close button, the View menu, show()),
        # not the main window's: quitting or minimizing Slicer does not count as closing it.
        self.toggleAction.connect("toggled(bool)", self._remember_shown)
        self.dock.connect("visibilityChanged(bool)", self._on_visibility)

    # ── Showing ──

    @property
    def shown(self):
        """Whether the dock is meant to be on screen (not closed by the user)."""
        return not self.dock.isHidden()

    def show(self):
        self.dock.setVisible(True)

    def _remember_shown(self, shown):
        qt.QSettings().setValue(config.SETTINGS_PANEL_SHOWN, bool(shown))

    def add_listener(self, listener):
        """Call ``listener()`` whenever the chat starts or stops or the status line changes.
        Returns the function that removes it."""
        self._listeners.append(listener)

        def remove_listener():
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove_listener

    def _changed(self):
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:
                logger.exception("SlicerFlow: a chat panel listener failed")

    @property
    def status(self):
        """The status line's text ("" when it is hidden)."""
        return self.statusLabel.text

    def _show_status(self, text, action=None, kind=None):
        """Show ``text`` in the status line ("" hides it), with ``action`` as its button:
        ``(label, function)``, or None for no button. ``kind`` names a line something may
        clear later ("unreachable")."""
        self._kind = kind
        self.statusLabel.text = text
        self.privacyLink.setVisible(text == config.CONSENT_NOTICE)
        self._action = action[1] if action else None
        self.actionButton.text = action[0] if action else ""
        self.actionButton.setVisible(action is not None)
        self.statusRow.setVisible(bool(text))
        # No processEvents(): it would run the chat's queued main-thread work in the middle
        # of this call. repaint() paints the line now and runs nothing else.
        self.statusRow.repaint()
        self._changed()

    def _on_action(self):
        action = self._action
        if action is not None:
            action()

    # ── Starting by itself ──

    def enable_autostart(self):
        """Start the chat by itself: on the next event-loop turn when the dock is shown (so
        Slicer's window paints first), or the first time it is shown. Once per dock, and
        only once the user has started it themselves before (``consented``); until then
        the dock shows the notice and waits for Start chat. The caller checks
        ``config.autostart_allowed``."""
        self._autostart_pending = True
        qt.QTimer.singleShot(0, self._autostart)  # a closed dock waits for _on_visibility

    def _on_visibility(self, visible):
        if visible and self._autostart_pending:
            qt.QTimer.singleShot(0, self._autostart)

    def _autostart(self):
        if not self._autostart_pending or not self.shown:
            return
        self._autostart_pending = False
        if self.running or not consented():
            return
        if sdk.needs_install():  # never pip at Slicer's startup: Start chat installs it
            self._show_status(INSTALL_NOTICE, ("Start chat", lambda: self.start()))
            return
        self.start(install_packages=False)

    # ── The chat ──

    @property
    def running(self):
        return lifecycle.current() is not None

    def start(self, connection=None, *, install_packages=True):
        """Start the chat and show it here, replacing a running one. ``connection`` None
        uses ``saved_connection()``. Returns None, or why it did not start, which the status
        line shows too. Never raises for a refusal the user can act on.

        Only the user starts it the first time (Start chat, here or in the module panel,
        under the notice): that is the opt-in, remembered before anything is installed or
        sent. ``install_packages`` False (the start by itself with Slicer) never runs pip:
        no SDK install, no ``sounddevice`` for the mic."""
        self._autostart_pending = False
        qt.QSettings().setValue(config.SETTINGS_CONSENTED, True)
        lifecycle.install_quit_hook()
        if connection is None:
            connection = saved_connection()
        self.show()
        problem = config.connection_problem(connection)
        if problem:
            return self._failed(problem, None)
        retry = ("Try again", lambda: self.start(connection))
        try:
            if install_packages and sdk.needs_install():
                self._show_status("Installing the Talk2View SDK into Slicer's Python…")
                sdk.install(force=sdk.installed())
            self._show_status("Starting the chat…")
            # Imported here: Runtime needs the SDK, which sdk.install() may just have put in.
            from .runtime import Runtime

            runtime = Runtime(connection.partner_key, connection.engine_url, self.web.setUrl,
                              on_unreachable=self._unreachable, on_reachable=self._reachable,
                              install_packages=install_packages)
            lifecycle.start(runtime)
        except Exception as exc:
            return self._start_failed(exc, connection, retry)
        self.connection = connection
        self._show_status("")
        return None

    def _start_failed(self, error, connection, retry):
        """A chat lock refusal is an expected state, told without a traceback. It is known by
        what it carries (``path``), not by its class: ``sdk.install()`` may have re-imported
        ``lifecycle``, and its errors are then not instances of the class this file bound."""
        path = getattr(error, "path", None)
        if not path:
            logger.exception("SlicerFlow: could not start the chat")
            return self._failed("Could not start the chat: {}".format(error), retry)
        logger.info("SlicerFlow: the chat did not start: %s", error)
        if getattr(error, "removable", False):
            return self._failed(str(error), (
                "Remove the lock and start",
                lambda: self._remove_lock_and_start(path, connection),
            ))
        return self._failed(str(error), ("Start chat", retry[1]))

    def _remove_lock_and_start(self, path, connection):
        """Remove a lock left on another computer, then start. Qt refuses to remove one
        whose process is alive, so this can never cut a running chat loose."""
        from .lifecycle import ChatLock  # the current copy (see _start_failed)

        if not ChatLock(path).remove_stale_lock():
            return self._failed(
                "SlicerFlow could not remove the lock: it is still held, so that chat may "
                "still be running.\nLock file: {}".format(path),
                ("Try again", lambda: self.start(connection)),
            )
        return self.start(connection)

    def _failed(self, message, action):
        self._show_status(message, action)
        return message

    def _unreachable(self, error):
        connection = self.connection
        if not self.running or connection is None:
            return
        if getattr(error, "type", None) == "network_error":  # talk2view.NetworkError
            text = ("Talk2View cannot reach {}. Check your internet connection, then press "
                    "Try again.".format(connection.engine_url))
        else:  # the check itself failed: say what happened, not that the network is down
            text = "Could not check the connection to {}: {}".format(
                connection.engine_url, error)
        self._show_status(text, ("Try again", self._check_engine), kind="unreachable")

    def _check_engine(self):
        """Ask the running chat to reach the engine again; the chat itself carries on."""
        runtime = lifecycle.current()
        if runtime is None or self.connection is None:
            return
        self._show_status(
            "Checking the connection to {}…".format(self.connection.engine_url),
            kind="unreachable")
        runtime.check_engine()

    def _reachable(self):
        """The engine answered, or someone signed in: an "unreachable" line is out of date."""
        if self._kind == "unreachable":
            self._show_status("")

    def stop(self):
        """Stop the chat and blank the web view."""
        lifecycle.stop()
        self.connection = None
        self.web.setUrl("about:blank")
        self._show_stopped()

    def _show_stopped(self):
        text = STOPPED if consented() else config.CONSENT_NOTICE
        self._show_status(text, ("Start chat", lambda: self.start()))

    # ── Teardown ──

    def destroy(self):
        """Take the dock out of the main window and the View menu, for good."""
        self._autostart_pending = False
        self._listeners = []
        self.toggleAction.disconnect("toggled(bool)", self._remember_shown)
        self.dock.disconnect("visibilityChanged(bool)", self._on_visibility)
        if self.menu is not None:
            self.menu.removeAction(self.toggleAction)
        self.window.removeDockWidget(self.dock)
        self.dock.setParent(None)
        self.dock.deleteLater()
