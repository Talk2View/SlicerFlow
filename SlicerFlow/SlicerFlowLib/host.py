"""The Talk2View host adapter for 3D Slicer.

``talk2view.ui.UIServer`` asks its host for: running code on the main thread (``post``),
showing the chat URL (``show``), opening links, setting the clipboard, the look
(``theme``) and where to keep the sign-in (``storage``). Slicer's Qt is PythonQt, so a
``qt.QTimer`` on the main thread drains the queue that ``post`` fills: a
``QTimer.singleShot`` started from another thread never fires.

``data_storage()`` is not part of ``HostAdapter`` -- ``UIServer`` never reads it -- it is
this class's own second file, next to ``storage()``'s: ``Runtime`` gives it to
``Talk2View(data_storage=...)`` so the chat list is kept separately from the sign-in.

The queue is measured as it runs (``pump``, a ``perf.PumpMonitor``): how long each posted
callable waited, and every tick of that timer that came more than ``perf.STALL_MS`` late,
when Slicer's main thread was busy with something else.
"""

import qt
import slicer

from talk2view import FileStorage, TimerPumpDispatcher
from talk2view.ui import Theme

from . import paths, perf

#: How often the main thread runs what other threads posted (every 20 ms).
DRAIN_MS = 20


class SlicerHost:
    """A ``talk2view.ui.HostAdapter`` (and ``Dispatcher``) for 3D Slicer.

    Build it on the main thread. ``show_url`` is called on the main thread with the chat
    URL: the module panel points its web view at it. ``capture`` is the microphone
    (``audio.SlicerAudioCapture``, or None where it cannot work here) that ``runtime.py``
    built before this host, once; ``audio()`` just hands it back.
    """

    name = "slicer"
    supports_clipboard = True

    def __init__(self, show_url, capture=None):
        self._timer = None
        self._drain = None
        self.pump = perf.PumpMonitor(DRAIN_MS)
        self._dispatcher = TimerPumpDispatcher(self._start_timer, DRAIN_MS)
        self._show_url = show_url
        self._capture = capture
        self._storage = FileStorage(paths.token_file())
        # Separate from the sign-in file, so a reply (written ~500 ms after it settles)
        # does not rewrite the token file, which is also rewritten on every refresh.
        self._data_storage = FileStorage(paths.conversations_file())

    def _start_timer(self, callback, interval_ms):
        self._drain = callback
        timer = qt.QTimer()
        timer.setInterval(interval_ms)
        timer.connect("timeout()", self._tick)
        timer.start()
        self._timer = timer  # kept: a QTimer that is garbage-collected stops firing
        return timer

    def _tick(self):
        self.pump.tick()
        self._drain()

    # ── Dispatcher ──

    def post(self, fn):
        stamp = self.pump.posted()
        pump = self.pump

        def timed():
            started = pump.ran(stamp)
            try:
                fn()
            finally:
                pump.done(started)

        self._dispatcher.post(timed)

    def is_main_thread(self):
        return self._dispatcher.is_main_thread()

    # ── HostAdapter (called on the main thread, through post) ──

    def show(self, url):
        self._show_url(url)

    def open_external(self, url):
        qt.QDesktopServices.openUrl(qt.QUrl(url))

    def clipboard_set(self, text):
        qt.QApplication.clipboard().setText(text)

    def theme(self):
        window = slicer.app.palette().color(qt.QPalette.Window)
        return Theme(mode="dark" if window.lightness() < 128 else "light")

    def storage(self):
        return self._storage

    def data_storage(self):
        return self._data_storage

    def audio(self):
        return self._capture

    def close(self):
        """Stop draining the main-thread queue. What is still queued never runs."""
        if self._timer is not None:
            self._timer.stop()
