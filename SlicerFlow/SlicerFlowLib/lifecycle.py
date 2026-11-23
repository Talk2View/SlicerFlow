"""The one chat of this 3D Slicer process, and the one chat per sign-in file.

The running ``Runtime`` is kept on ``slicer.modules`` (as ``slicerflowRuntime``), not in a
global of this file. The module's Reload re-imports this file, and a global would forget a
server that is still running; the reloaded code finds it on ``slicer.modules`` and stops
it. ``install_quit_hook()`` connects ``aboutToQuit`` once per process, however often the
module is reloaded.

``ChatLock`` keeps a second Slicer process off the same sign-in file while a chat runs:
the engine rotates the refresh token on every refresh, so two processes sharing
``auth.json`` would sign each other out.

A guest session is kept in ``auth.json`` like an account's, so the next launch carries
on as the same guest, as the Talk2View website does.

Everything here runs on Slicer's main thread.
"""

import logging
import os
import socket

import qt
import slicer

from . import paths

RUNTIME_ATTR = "slicerflowRuntime"
QUIT_HOOK_ATTR = "slicerflowQuitHookInstalled"

logger = logging.getLogger("SlicerFlow")

#: ``qt.QLockFile.LockError``. Only the first is contention; the others mean the lock file
#: itself cannot be used, which is a different thing to tell the user.
LOCK_FAILED = 1
PERMISSION_DENIED = 2


class ChatLockError(RuntimeError):
    """SlicerFlow could not take its chat lock.

    ``path`` is the lock file. ``holder`` is ``(pid, app, hostname)`` read from it, or None
    when there was nothing to read. ``removable`` marks the one case the user can clear: a
    lock written on another computer, where Qt cannot ask whether that process still runs
    (see ``ChatLock``).
    """

    def __init__(self, message, path, holder=None, removable=False):
        RuntimeError.__init__(self, message)
        self.path = path
        self.holder = holder
        self.removable = removable


class ChatAlreadyRunningError(ChatLockError):
    """Another 3D Slicer holds the chat lock."""


def this_computer():
    """The name Qt writes into a lock file (``QSysInfo::machineHostName()``)."""
    return socket.gethostname()


class ChatLock:
    """``<home>/chat.lock``, a ``qt.QLockFile``.

    Qt frees the lock of a process that died **on this computer**, because it checks the
    process id in the file, so a crash never leaves the chat locked for good. That check is
    what makes ``setStaleLockTime(0)`` right here: age alone never makes a lock stale, so a
    chat left open for hours is never stolen from a live Slicer. Qt cannot check a process
    on another computer, though, which is what a home directory shared between machines
    gives it. Only the user knows whether that chat is gone, so ``acquire()`` offers them
    ``remove_stale_lock()`` rather than deciding for them.
    """

    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._lock = qt.QLockFile(path)
        self._lock.setStaleLockTime(0)  # stale only when its process is gone, never by age

    def acquire(self):
        """Take the lock, or raise a ``ChatLockError`` that says what stopped it."""
        if self._lock.tryLock(0):
            return
        error = self._lock.error()
        if error != LOCK_FAILED:  # not contention at all: the file itself cannot be used
            raise ChatLockError(self._unusable_message(error), self.path)
        holder = self.holder()
        # Qt leaves the host line empty when it cannot name one, and reads that back as
        # this computer, so we say the same: never "another computer, " with a blank name,
        # and never offer to remove a lock this computer may be holding.
        hostname = holder[2] if holder is not None else ""
        elsewhere = bool(hostname) and hostname.lower() != this_computer().lower()
        raise ChatAlreadyRunningError(
            self._held_message(holder, elsewhere), self.path, holder, removable=elsewhere
        )

    def holder(self):
        """``(pid, app, hostname)`` from the lock file, or None when it cannot be read.

        Qt writes those three lines first. ``QLockFile.getLockInfo()`` would give the same
        three, but PythonQt cannot call it: its out-parameters raise ``ValueError``.
        """
        try:
            with open(self.path, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().split("\n")
        except OSError:  # released (and deleted) in between, or unreadable
            return None
        if len(lines) < 3:
            return None
        return (lines[0].strip(), lines[1].strip(), lines[2].strip())

    def remove_stale_lock(self):
        """Delete a lock file whose process is gone; False while it is still held.

        Qt refuses as long as the holder lives, so this can never cut a running chat loose:
        a wrong guess about which computer holds the lock fails closed.
        """
        return bool(self._lock.removeStaleLockFile())

    def release(self):
        self._lock.unlock()

    def _held_message(self, holder, elsewhere):
        if holder is None:
            return (
                "Another 3D Slicer is using your Talk2View sign-in. Stop the chat there, "
                "then press Start chat here.\nLock file: {}".format(self.path)
            )
        pid, app, hostname = holder
        if elsewhere:
            return (
                "Your Talk2View sign-in is locked by {} (process {}) on another computer, "
                "{}, which shares this home directory. If no chat is running there any "
                "more, remove the lock and press Start chat again.\n"
                "Lock file: {}".format(app or "3D Slicer", pid, hostname, self.path)
            )
        return (
            "The SlicerFlow chat is already open in another 3D Slicer window on this "
            "computer (process {}). Stop it there (or close that window), then press Start "
            "chat here.\nLock file: {}".format(pid, self.path)
        )

    def _unusable_message(self, error):
        if error == PERMISSION_DENIED:
            return (
                "SlicerFlow may not create its lock file: permission denied. Check that "
                "you can write to that folder.\nLock file: {}".format(self.path)
            )
        return (
            "SlicerFlow could not lock your Talk2View sign-in (Qt error {}).\n"
            "Lock file: {}".format(error, self.path)
        )


def current():
    """The running runtime, or None."""
    return getattr(slicer.modules, RUNTIME_ATTR, None)


def start(runtime):
    """Stop the running runtime, if any, then start ``runtime`` and keep it. If its
    ``start()`` raises, it is stopped (it unwinds whatever it had built) and the error is
    raised. Returns the chat URL."""
    stop()
    try:
        url = runtime.start()
    except BaseException:
        runtime.stop()
        raise
    setattr(slicer.modules, RUNTIME_ATTR, runtime)
    return url


def stop():
    """Stop the running runtime, if any. Safe to call any number of times."""
    runtime = current()
    setattr(slicer.modules, RUNTIME_ATTR, None)
    if runtime is not None:
        runtime.stop()


def install_quit_hook():
    """Stop the runtime when Slicer quits."""
    if getattr(slicer.modules, QUIT_HOOK_ATTR, False):
        return
    slicer.app.connect("aboutToQuit()", on_quit)
    setattr(slicer.modules, QUIT_HOOK_ATTR, True)


def on_quit():
    # After a Reload this may be the previous copy of this function: it still works, because
    # the runtime it stops is read from slicer.modules, not from this file's globals.
    stop()
