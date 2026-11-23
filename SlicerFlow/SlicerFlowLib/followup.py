"""Screenshots the model takes with ``capture_view``, sent to it in a follow-up message.

The engine accepts attachments only on user messages, and a tool's result is text, so a
screenshot cannot come back as ``capture_view``'s result. The tool hands the PNG to
``FollowUps`` instead. When the run that took it has ended, one automatic user message
carries every screenshot of that run, uploaded with the SDK:

    (Sent by SlicerFlow) The screenshots capture_view took: 3D view.   + the images

and the assistant answers it like any other message. The follow-up is not sent when the
run ended with an error, when the user pressed Stop or started a new chat, or when someone
else is signed in by then — any of which counts right up to the moment the message goes
out, the upload included, so a batch never lands in the conversation that replaced the one
it was captured in. While the run waits for an approval, the follow-up waits too.
It never cascades on its own: ``capture_view`` refuses during a follow-up's own run, so a
new screenshot needs a new request from the user (or their approval of one).

Pure Python (``threading`` and the SDK): no Slicer needed, so it is tested without it.
"""

import dataclasses
import logging
import threading

from talk2view import ChatController, ControllerBusyError, ControllerClosedError, T2VError

MAX_PER_RUN = 3
FOLLOW_UP_TEXT = "(Sent by SlicerFlow) The screenshots capture_view took: {}."
#: How often a follow-up waiting on an approval looks again.
APPROVAL_POLL_S = 0.25

logger = logging.getLogger("SlicerFlow")


class FollowUpController(ChatController):
    """The ``ChatController`` the chat page drives. It tells its stop listeners when the
    user presses Stop (``cancel``), starts a new chat the old way (``clear``, still what
    ``newSession`` reaches), or a conversation is replaced by any path -- New chat with the
    thread list on, a thread switch, a deleted current chat, a sign-in that blanks the
    chat -- through the SDK's own ``conversationReplaced`` notice, and it answers every
    "always" as "once".

    An earlier version of this class overrode ``restore_conversation`` alone to catch a
    switch. Dead code: ``ThreadsState`` (and any other list) switches with
    ``exchange_conversation``, which never called it (reproduced against the SDK: a
    follow-up's screenshots landed in the thread the user had switched to). ``conversationReplaced`` fires for both, so one subscription covers
    every way the conversation on screen can change out from under a follow-up in flight,
    without this class needing to know which SDK method did it.

    That notice arrives AFTER the swap (queued inside
    ``_apply_restore_locked``, delivered by the flush ``_after_restore`` makes once the
    lock is released) -- a microsecond window in which a follow-up whose ``_still_ours``
    check passes the instant before an ``exchange_conversation`` takes the lock has its
    ``send()`` queued behind the swap and lands in the thread just switched TO. The
    ``exchange_conversation`` override below closes it by telling the stop listeners
    FIRST, before delegating; the ``conversationReplaced`` subscription stays as the
    general mechanism (``restore_conversation``, and anything else that ever reaches
    ``_apply_restore_locked``).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._stop_listeners = []
        self.on("conversationReplaced", self._notify_stop)

    def approve(self, decision, *, tool_call_id=None):
        """Answer the pending approval, with an "always" answered as "once".

        Every SlicerFlow tool that asks, asks every time: one approval is one call, never
        a standing permission for the rest of the conversation. One "always" on
        ``capture_view`` would let it upload slice views — patient details in the corner
        text and all — without asking again, and one on ``load_file`` would let it read
        whatever path the model names next. "once" and "deny" go through untouched.

        The chat page draws no "Always allow" button, but the page is not the gate: the
        decision arrives as a localhost HTTP command, which anything on this computer can
        post, and the page itself may be replaced.

        Nothing is read to decide this, and that is the point. Looking the pending card up
        would rest the refusal on a read that can have moved on between the reading and the
        answering — one that named another tool would carry the "always" through to the
        card that really is up, and pinning the answer to the id it read does not help when
        the id is the only part that is still right. There is nothing to look up: no tool
        here may be always allowed.
        """
        if decision.action == "always":
            decision = dataclasses.replace(decision, action="once")
        super().approve(decision, tool_call_id=tool_call_id)

    def add_stop_listener(self, callback):
        self._stop_listeners.append(callback)

    def _notify_stop(self):
        for callback in list(self._stop_listeners):
            try:
                callback()
            except Exception:
                logger.exception("SlicerFlow: a stop listener failed")

    def cancel(self):
        self._notify_stop()
        super().cancel()

    def clear(self):
        """``newSession``'s old path: a DELETE, not through ``_apply_restore_locked``, so
        it raises no ``conversationReplaced`` of its own -- told explicitly, like
        ``cancel``."""
        self._notify_stop()
        super().clear()

    def exchange_conversation(self, snapshot, *, epoch, carry=None):
        """Told first, before the swap, not after it through
        ``conversationReplaced`` -- closes the window a follow-up's own ``_still_ours``
        check could otherwise pass through in the same instant. Only when ``snapshot`` is
        not None: an export-only call (``snapshot=None``) replaces nothing. A refused
        exchange (a stale ``epoch``) still drops a batch here, harmlessly early -- the
        identity change it lost to was about to drop it anyway, through
        ``FollowUps.on_account_change``."""
        if snapshot is not None:
            self._notify_stop()
        return super().exchange_conversation(snapshot, epoch=epoch, carry=carry)


def _start_thread(fn):
    # A daemon, and never joined: an upload the SDK is already reading cannot be
    # interrupted by closing its client, so this thread can outlive Stop chat by as long as
    # the upload timeout. It must not be able to hold up quitting Slicer while it does.
    threading.Thread(target=fn, name="sf-follow-up", daemon=True).start()


class FollowUps:
    """``add()`` runs on Slicer's main thread (inside ``capture_view``); the delivery runs
    on one ``sf-follow-up`` thread per batch, which waits for the run to end, uploads the
    images and sends the message.

    ``upload(png, filename)`` returns a ``talk2view.Attachment``; ``identity()`` returns
    the signed-in user's id, or None.
    """

    def __init__(self, controller, upload, identity, start_thread=_start_thread):
        self._controller = controller
        self._upload = upload
        self._identity = identity
        self._start_thread = start_thread
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._pending = []  # [(png, label, number)] taken during the current run
        self._owner = None  # who was signed in when the first of them was taken
        self._epoch = 0  # +1 whenever the conversation they belong to ends (``drop``)
        self._taken = 0  # screenshots taken in this chat, for their filenames
        self._delivering = False  # a sf-follow-up thread is waiting or sending
        self._in_follow_up = False  # the follow-up's own run is going

    def add(self, png, label):
        """Keep a screenshot for the follow-up; returns what ``capture_view`` tells the model."""
        with self._lock:
            if self._closed.is_set():
                raise ValueError("The chat is closing.")
            if self._in_follow_up:
                raise ValueError(
                    "You are looking at the screenshots already. Take new ones in a later reply."
                )
            if len(self._pending) >= MAX_PER_RUN:
                raise ValueError("At most {} screenshots per reply.".format(MAX_PER_RUN))
            if not self._pending:
                self._owner = self._identity()
            # Numbered across the whole chat, not within the batch: two replies that each
            # capture the 3D view would otherwise show the model two different images under
            # one filename. The numbers also tell it the order they were taken in.
            self._taken += 1
            self._pending.append((png, label, self._taken))
            start = not self._delivering
            self._delivering = True
        if start:
            self._start_thread(self._deliver)
        return (
            "Captured the {} ({} KB). You will see it in the next message, which SlicerFlow "
            "sends after this reply. Finish this reply without describing the image."
        ).format(label, max(1, len(png) // 1024))

    def drop(self):
        """The user stopped the run or started a new chat: forget what it captured.

        The epoch moves with the queue. Emptying the queue is not enough on its own: a
        delivery already under way holds its batch in a local of its own, and the epoch is
        the only thing that tells that thread the conversation it captured them in is gone.
        """
        with self._lock:
            self._pending = []
            self._epoch += 1

    def on_account_change(self, _user=None):
        """``t2v.auth.on_auth_state_change``: every auth event drops the batch.

        Comparing user ids is not enough. A sign-out makes the controller forget the
        conversation, and signing straight back in as the same person leaves that fresh one
        in place, so the id reads the same either side and the batch would look like it
        still belonged. The event is the only witness, so it is what we listen to.

        It runs on whichever thread changed the auth state, so it only takes this queue's
        lock and returns; it never waits on the main thread or on a run.
        """
        self.drop()

    def close(self):
        self._closed.set()
        self.drop()

    def _still_ours(self, epoch, owner):
        """Is the conversation this batch was captured in still the live one? Call it
        holding ``self._lock``.

        ``drop()`` moves the epoch for Stop, New chat, an account change and close;
        ``owner`` is kept as a second check, for a switch of account between two runs.
        """
        if self._closed.is_set() or self._epoch != epoch:
            return False
        try:
            return self._identity() == owner
        except OSError:  # the sign-in file cannot be read: we cannot say it is still ours
            logger.warning("SlicerFlow: the signed-in user could not be read")
            return False

    @staticmethod
    def _log_the_chat_closed(exc):
        """Stop chat closes the SDK client one closer after this queue, and this thread is
        never joined, so an upload or a send in flight dies with it: httpx raises a plain
        ``RuntimeError`` when the client is closed under it, and the upload timeout ends a
        read already in progress with a ``T2VError``. An ordinary Stop must leave neither a
        warning nor a stack trace in the log."""
        logger.info("SlicerFlow: the chat closed before the screenshots were sent (%s)", exc)

    def _wait_for_the_run(self):
        """Wait until no run is going and no approval is waiting. False when closed."""
        while not self._closed.is_set():
            self._controller.wait_idle()
            if self._closed.is_set():
                return False
            snapshot = self._controller.snapshot()
            if snapshot["pendingApproval"] is not None:
                self._closed.wait(APPROVAL_POLL_S)  # the user has not answered yet
            elif not snapshot["isLoading"]:
                return True
            # else: a run started (an approval's resume) after wait_idle() returned
        return False

    def _deliver(self):
        try:
            if not self._wait_for_the_run():
                return
            with self._lock:
                pending, self._pending = self._pending, []
                owner, epoch = self._owner, self._epoch
            if not pending:
                return  # stopped, a new chat, or closed
            if self._controller.snapshot()["error"] is not None:
                logger.info("SlicerFlow: the run failed; its screenshots are not sent")
                return
            if self._identity() != owner or owner is None:
                logger.info("SlicerFlow: the account changed; the screenshots are not sent")
                return
            attachments = [
                self._upload(png, "{}-{}.png".format(label.lower().replace(" ", "-"), number))
                for png, label, number in pending
            ]
            # Uploading takes as long as the images take, and Stop, New chat or a sign-out
            # in that time ends the conversation these belong to. Their attachment ids are
            # dropped unused rather than posted into whatever replaced it: they are the
            # previous conversation's pictures of the previous scene.
            #
            # One check, in the same locked section as the send, so that deciding to send
            # and sending are a single step and no drop can land between them. An earlier
            # check as well would only mask this one. ``send()`` is documented
            # non-blocking: it returns once the job is on the executor. ``wait_idle()``
            # below must stay outside — it blocks, and ``add()`` runs on the main thread.
            with self._lock:
                if not self._still_ours(epoch, owner):
                    logger.info(
                        "SlicerFlow: the conversation these screenshots came from has "
                        "ended; they are not sent"
                    )
                    return
                self._in_follow_up = True
                try:
                    self._controller.send(
                        FOLLOW_UP_TEXT.format(
                            ", ".join(label for _png, label, _number in pending)),
                        attachments=attachments,
                    )
                except BaseException:
                    self._in_follow_up = False
                    raise
            try:
                self._controller.wait_idle()
            finally:
                with self._lock:
                    self._in_follow_up = False
        except (ControllerBusyError, ControllerClosedError, T2VError) as exc:
            if self._closed.is_set():
                self._log_the_chat_closed(exc)
            else:
                # Screenshots the user asked for that never reached the model: a busy chat,
                # an upload the engine refused, an image it would not take. Worth more than
                # a note. The model is told nothing — another message here could cascade.
                logger.warning("SlicerFlow: the screenshots were not sent (%s)", exc)
        except Exception as exc:
            if self._closed.is_set():
                self._log_the_chat_closed(exc)
            else:
                logger.exception("SlicerFlow: sending the screenshots failed")
        finally:
            with self._lock:
                self._delivering = False
                if self._pending and not self._closed.is_set():
                    # Taken while this batch was being sent: deliver them too.
                    self._delivering = True
                    restart = True
                else:
                    restart = False
            if restart:
                self._start_thread(self._deliver)
