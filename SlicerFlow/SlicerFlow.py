import logging
import os
import sys

import ctk
import qt
import slicer
from slicer.ScriptedLoadableModule import (
    ScriptedLoadableModule,
    ScriptedLoadableModuleLogic,
    ScriptedLoadableModuleTest,
    ScriptedLoadableModuleWidget,
)

try:
    from slicer.i18n import tr as _
    from slicer.i18n import translate
except ImportError:  # a 3D Slicer too old for SlicerFlow: the panel says so

    def _(text):
        return text

    def translate(context, text):
        return text


from SlicerFlowLib import chat_dock, config, lifecycle, panel_style, paths, perf, sdk, tools_panel

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
PARTNER_FILE = os.path.join(MODULE_DIR, "Resources", "partner.json")
#: The Talk2View logomark, Teal on transparent: the brand's in-app mark (the module's own
#: icon, Resources/Icons/SlicerFlow.png, is the app-icon tile). scripts/generate_brand_icons.py.
LOGO_FILE = os.path.join(MODULE_DIR, "Resources", "Icons", "Talk2ViewLogomark.png")
#: The logomark's width in the panel's header, in device-independent pixels.
LOGO_WIDTH = 40
FONTS_DIR = os.path.join(MODULE_DIR, "Resources", "Fonts")


#
# SlicerFlow
#


class SlicerFlow(ScriptedLoadableModule):
    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = _("SlicerFlow")
        self.parent.categories = [translate("qSlicerAbstractCoreModule", "Talk2View")]
        self.parent.dependencies = []
        self.parent.contributors = ["Andy Huynh (A2B Technology Corporation Pty Ltd)"]
        self.parent.helpText = _(
            "Chat with an AI assistant that can look at and change your scene. The chat is in "
            "the <b>SlicerFlow by Talk2View</b> panel on the right of the window (<b>View → "
            "SlicerFlow by Talk2View</b> shows it again after you close it). It runs on "
            "Talk2View's servers: nothing is sent until you press <b>Start chat</b> the first "
            "time, and from then on it starts with Slicer. Not for clinical use. Chat "
            "as a guest, within the limits Talk2View sets for guests, or sign in with your "
            "Talk2View account (Apple, Google, or email and password). <b>New chat</b> keeps "
            "the conversation in the chat's list (<b>Chats</b>) on this computer and starts "
            "another. This module keeps the "
            "chat's settings. Privacy: "
            "<a href=\"https://talk2view.com/legal/privacy-policy\">talk2view.com/legal/privacy-policy</a>."
        )
        self.parent.acknowledgementText = _(
            "Built by A2B Technology Corporation Pty Ltd (Talk2View) on the Talk2View Python SDK "
            "(MIT) and its localhost chat UI server."
        )
        # Once per process: Slicer builds the module object once, and a Reload does not.
        slicer.app.connect("startupCompleted()", onStartupCompleted)


def onStartupCompleted(testing=None, options=None):
    """Show the chat panel on the right of the main window and start the chat in it, on the
    next event-loop turn so Slicer's window paints first (``ChatDock.enable_autostart``).
    Nothing at all in ``--testing`` mode, in a scripted or windowless run
    (``config.SCRIPTED_RUN_OPTIONS``) or with ``SLICERFLOW_NO_AUTOSTART`` set: tests build
    the panel and start the chat themselves. ``testing`` and ``options`` replace what this
    Slicer says (for tests); Qt calls this with neither."""
    if slicer.util.mainWindow() is None:
        return
    if not config.slicer_supported(slicer.app.majorVersion, slicer.app.minorVersion):
        return
    if testing is None:
        testing = slicer.app.testingEnabled()
    if options is None:
        options = config.command_options(slicer.app.commandOptions())
    if not config.autostart_allowed(testing, os.environ, options):
        return
    try:
        dock = chat_dock.ensure()
        if dock is not None:
            dock.enable_autostart()
    except Exception:  # never let the chat panel break Slicer's startup
        logging.exception("SlicerFlow: could not open the chat panel")


#
# SlicerFlowWidget
#


class SlicerFlowWidget(ScriptedLoadableModuleWidget):
    def setup(self):
        ScriptedLoadableModuleWidget.setup(self)
        self.logic = SlicerFlowLogic()
        self.statusState = "idle"
        # The brand typeface for the header and the main button; None keeps Slicer's font.
        self.brandFamily = panel_style.load_brand_fonts(FONTS_DIR)
        # Everything below in one body that tells Slicer its height plainly: see FittedBody.
        body = panel_style.FittedBody()
        self.layout.addWidget(body)
        bodyLayout = qt.QVBoxLayout(body)
        bodyLayout.setContentsMargins(0, 0, 0, 0)
        bodyLayout.setSpacing(self.layout.spacing)
        top = qt.QWidget()
        bodyLayout.addWidget(top)
        column = qt.QVBoxLayout(top)
        column.setContentsMargins(4, 8, 4, 12)
        column.setSpacing(10)

        # The header: the logo, the product, and what it is.
        header = qt.QHBoxLayout()
        header.setSpacing(10)
        self.logoLabel = qt.QLabel()
        self.logoLabel.setPixmap(self._logo())
        header.addWidget(self.logoLabel, 0, qt.Qt.AlignVCenter)
        titles = qt.QVBoxLayout()
        titles.setSpacing(0)
        self.titleLabel = qt.QLabel(_("SlicerFlow"))
        self.titleLabel.setFont(panel_style.brand_font(
            self.titleLabel, self.brandFamily, point_delta=4, weight=qt.QFont.Bold))
        titles.addWidget(self.titleLabel)
        self.subtitleLabel = panel_style.muted_label(_("AI assistant for 3D Slicer, by Talk2View"))
        self.subtitleLabel.setFont(panel_style.brand_font(self.subtitleLabel, self.brandFamily))
        titles.addWidget(self.subtitleLabel)
        header.addLayout(titles, 1)
        column.addLayout(header)

        # The chat's state: a coloured dot and one line (the chat panel's own, when it has one).
        status = qt.QHBoxLayout()
        status.setSpacing(6)
        self.statusDot = qt.QLabel("\u25cf")  # ●
        self.statusDot.setSizePolicy(qt.QSizePolicy.Fixed, qt.QSizePolicy.Preferred)
        status.addWidget(self.statusDot, 0, qt.Qt.AlignTop)
        self.statusLabel = qt.QLabel()
        self.statusLabel.wordWrap = True
        status.addWidget(self.statusLabel, 1)
        column.addLayout(status)

        self.startButton = qt.QPushButton(_("Start chat"))
        self.startButton.minimumHeight = 34
        self.startButton.setFont(panel_style.brand_font(
            self.startButton, self.brandFamily, weight=qt.QFont.DemiBold))
        self._styleStartButton(False)
        column.addWidget(self.startButton)

        # The user's own tool packs: listed, turned on or off, applied by a restart.
        self.toolsSection = tools_panel.ToolsSection(bodyLayout, restart=self._restartChat)

        # Debugging: uncomment this block (and the matching lines marked "Performance") to
        # show the last messages' timings in the panel.
        #
        # # Performance: how long the last messages took, step by step (SlicerFlowLib.perf).
        # # Read from the running chat once a second while the section is open.
        # self.performanceBox = ctk.ctkCollapsibleButton()
        # self.performanceBox.text = _("Performance")
        # self.performanceBox.collapsed = True
        # self.layout.addWidget(self.performanceBox)
        # performance = qt.QVBoxLayout(self.performanceBox)
        # self.performanceText = qt.QPlainTextEdit()
        # self.performanceText.readOnly = True
        # self.performanceText.toolTip = _(
        #     "The last messages, newest first: how long until the first words, the engine "
        #     "and model's wait, and each Slicer tool's wait for Slicer, its run and the "
        #     "engine's resume after it."
        # )
        # self.performanceText.setPlainText(perf.format_recent([]))
        # performance.addWidget(self.performanceText)
        # self.logFolderButton = qt.QPushButton(_("Open log folder"))
        # self.logFolderButton.toolTip = _(
        #     "The folder with metrics.jsonl: one line of timings per message (no message "
        #     "text), for a slow chat to be looked into."
        # )
        # performance.addWidget(self.logFolderButton)
        # self._performanceShown = None
        # self._performanceTimer = qt.QTimer()
        # self._performanceTimer.setInterval(1000)
        # self._performanceTimer.connect("timeout()", self._refreshPerformance)

        self.layout.addStretch(1)

        self.chatDock = None
        self._unlisten = None
        self.startButton.connect("clicked(bool)", self.onStartStop)
        # Performance: self.logFolderButton.connect("clicked(bool)", self.onOpenLogFolder)
        # Performance: self.performanceBox.connect(
        #     "contentsCollapsed(bool)", self._onPerformanceCollapsed)
        lifecycle.install_quit_hook()
        if not config.slicer_supported(slicer.app.majorVersion, slicer.app.minorVersion):
            self._setBusy(True)
            self._status(config.unsupported_message(slicer.app.applicationVersion))
            return
        # The one chat panel of this process: opening the module never builds a second.
        self.chatDock = chat_dock.ensure()
        if self.chatDock is None:
            self.startButton.enabled = False
            self._status(_(
                "The chat needs 3D Slicer's main window, and this Slicer runs without one."))
            return
        self._unlisten = self.chatDock.add_listener(self._refresh)
        self._refresh()

    def cleanup(self):
        # Performance: self._performanceTimer.stop()
        if self._unlisten is not None:
            self._unlisten()
            self._unlisten = None
        self.logic.stop()

    def onReload(self):
        """Stop the chat, take the chat panel down and forget ``SlicerFlowLib``, so the
        reloaded module imports the library's current code too (Slicer's Reload re-imports
        only this file) and builds a fresh panel from it."""
        lifecycle.stop()
        chat_dock.remove()
        for name in [n for n in sys.modules if n == "SlicerFlowLib" or n.startswith("SlicerFlowLib.")]:
            del sys.modules[name]
        ScriptedLoadableModuleWidget.onReload(self)

    def _status(self, text, state="problem"):
        # No processEvents() here: it would run the chat's queued main-thread work in the
        # middle of this handler. repaint() paints the label now and runs nothing else.
        self.statusState = state
        self.statusLabel.text = text
        panel_style.set_text_color(self.statusDot, panel_style.dot_color(self.statusDot, state))
        self.statusLabel.repaint()
        self.statusDot.repaint()

    def _setBusy(self, busy):
        self.startButton.enabled = not busy

    def _logo(self):
        """The logomark ``LOGO_WIDTH`` wide, sharp on a high-DPI screen."""
        ratio = self.logoLabel.devicePixelRatioF()
        pixmap = qt.QPixmap(LOGO_FILE).scaledToWidth(
            int(LOGO_WIDTH * ratio), qt.Qt.SmoothTransformation)
        pixmap.setDevicePixelRatio(ratio)
        return pixmap

    def _styleStartButton(self, running):
        """Start chat is the panel's one main action, in the accent colour; Stop chat is an
        ordinary button."""
        self.startButton.setStyleSheet(
            "" if running else panel_style.primary_button_style(self.startButton, self.brandFamily))

    def _statusLine(self, running, dockStatus):
        """``(text, state)`` for the status line. The chat panel's own line wins: a step
        under way ends in "…" ("busy"), its "Chat stopped." and the notice a chat never
        started shows are "idle", anything else is a problem the user may need to act on."""
        if dockStatus:
            if dockStatus.endswith("…"):
                return dockStatus, "busy"
            if dockStatus == chat_dock.STOPPED:
                return "Chat stopped", "idle"
            if dockStatus in (config.CONSENT_NOTICE, chat_dock.INSTALL_NOTICE):
                return dockStatus, "idle"  # what Start chat will do: news, not a problem
            return dockStatus, "problem"
        if running:
            return _("Chat running"), "ok"
        return _("Chat stopped"), "idle"

    def _refresh(self):
        """Follow the chat panel: the button says what it will do, and the status line
        repeats the panel's, or says whether the chat runs."""
        running = self.logic.running
        self.startButton.text = _("Stop chat") if running else _("Start chat")
        self._styleStartButton(running)
        panel = self.chatDock
        text, state = self._statusLine(running, panel.status if panel is not None else "")
        self._status(text, state)
        self.statusLabel.toolTip = self._statusDetail(running, panel)
        self.toolsSection.refresh()

    def _statusDetail(self, running, panel):
        """The status line's tooltip: where the chat runs, or which SDK is installed."""
        if running and panel is not None and panel.connection is not None:
            return _("Connected to {}").format(panel.connection.engine_url)
        if sdk.installed():
            return _("Talk2View SDK {} is installed.").format(sdk.version())
        return _("The Talk2View SDK installs when you start the chat.")

    def connection(self):
        """The partner key and engine this panel would start the chat with: the
        environment (T2V_PARTNER_KEY / T2V_BASE_URL, for developers), else the bundled
        Resources/partner.json."""
        return config.resolve(os.environ, config.load_bundled(PARTNER_FILE))

    # Performance: def onOpenLogFolder(self):
    # Performance:     folder = paths.home()
    # Performance:     os.makedirs(folder, exist_ok=True)
    # Performance:     self._openFolder(folder)
    #
    # Performance: @staticmethod
    # Performance: def _openFolder(folder):
    # Performance:     qt.QDesktopServices.openUrl(qt.QUrl.fromLocalFile(folder))
    #
    # Performance: def _onPerformanceCollapsed(self, collapsed):
    # Performance:     if collapsed:
    # Performance:         self._performanceTimer.stop()
    # Performance:     else:
    # Performance:         self._refreshPerformance()
    # Performance:         self._performanceTimer.start()
    #
    # Performance: def _refreshPerformance(self):
    # Performance:     """Show the running chat's last turns; keep what is shown when no
    # Performance:     chat runs."""
    # Performance:     runtime = lifecycle.current()
    # Performance:     performance = getattr(runtime, "performance", None)
    # Performance:     if performance is None:
    # Performance:         return
    # Performance:     shown = (id(performance), performance.version)
    # Performance:     if shown != self._performanceShown:
    # Performance:         self._performanceShown = shown
    # Performance:         self.performanceText.setPlainText(performance.text())

    def onStartStop(self):
        if self.logic.running:
            self.chatDock.stop()
            return
        self._setBusy(True)
        try:
            # The panel shows why it did not start (a lock, the SDK install, the engine
            # URL), and _refresh repeats it here.
            self.chatDock.start(self.connection())
        finally:
            self._setBusy(False)
        self._refresh()

    def _restartChat(self):
        """Start the chat again, so it loads the tool packs as they are now. Nothing to do
        when no chat runs: the next Start chat loads them."""
        if self.chatDock is not None and self.logic.running:
            self.chatDock.start(self.connection())  # replaces the running chat


#
# SlicerFlowLogic
#


class SlicerFlowLogic(ScriptedLoadableModuleLogic):
    """Starts and stops the process's one chat (``SlicerFlowLib.lifecycle``)."""

    @property
    def running(self):
        return lifecycle.current() is not None

    def start(self, partner_key, base_url, show_url):
        """Start a chat, replacing any running one. Returns its URL."""
        from SlicerFlowLib.runtime import Runtime

        return lifecycle.start(Runtime(partner_key, base_url, show_url))

    def stop(self):
        lifecycle.stop()


#
# SlicerFlowTest
#


class SlicerFlowTest(ScriptedLoadableModuleTest):
    """The quick self-test ("Reload and Test"). The full suite is in Testing/Python; see
    README.md."""

    def runTest(self):
        self.test_bundled_files()

    def test_bundled_files(self):
        self.delayDisplay("Checking the SDK's chat page")
        if sdk.installed():
            from talk2view.ui.static import default_static_dir

            self.assertTrue((default_static_dir() / "index.html").is_file())
        self.delayDisplay("SDK files OK")
