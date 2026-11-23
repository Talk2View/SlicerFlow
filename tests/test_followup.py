"""FollowUps against a scripted controller: when the screenshots are sent, and when not."""

import logging
import threading
import time

import pytest

from talk2view import Attachment, ControllerBusyError, T2VError, Talk2View
from SlicerFlowLib import followup

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 3000


class ScriptedController:
    """wait_idle/snapshot/send, with runs the test starts and ends."""

    def __init__(self):
        self.idle = threading.Event()
        self.idle.set()
        self.state = {"error": None, "pendingApproval": None, "isLoading": False}
        self.sent = []
        self.busy = False

    def begin_run(self):
        self.state = dict(self.state, isLoading=True)
        self.idle.clear()

    def end_run(self, error=None, approval=None):
        self.state = {"error": error, "pendingApproval": approval, "isLoading": False}
        self.idle.set()

    def resume_run(self):
        """The user answered the approval: the card is gone and the resume run has begun,
        but the idle flag has not been cleared yet. ``wait_idle()`` is documented as
        returning in exactly this window, so the follow-up has only ``isLoading`` to go on.
        """
        self.state = {"error": None, "pendingApproval": None, "isLoading": True}
        self.idle.set()

    def wait_idle(self, timeout=None):
        return self.idle.wait(timeout)

    def snapshot(self):
        return dict(self.state)

    def send(self, text, attachments=None):
        if self.busy:
            raise ControllerBusyError("busy")
        self.sent.append((text, [a.filename for a in attachments or []]))
        self.begin_run()


class BlockingUpload:
    """An upload the test holds open, so the window a Stop, a New chat or a sign-out has to
    land in is the test's to choose rather than a sleep's to guess."""

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.filenames = []

    def __call__(self, png, filename):
        self.filenames.append(filename)
        self.started.set()
        if not self.release.wait(5):
            raise AssertionError("the upload was never released")
        return Attachment(id="att-{}".format(len(self.filenames)), filename=filename,
                          mime_type="image/png", size_bytes=len(png))


def _wait(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


@pytest.fixture
def rig():
    controller = ScriptedController()
    uploads = []
    who = {"id": "user-1"}

    def upload(png, filename):
        uploads.append(filename)
        return Attachment(id="att-{}".format(len(uploads)), filename=filename,
                          mime_type="image/png", size_bytes=len(png))

    follow_ups = followup.FollowUps(controller, upload, lambda: who["id"])
    yield controller, follow_ups, uploads, who
    follow_ups.close()


@pytest.fixture
def held_rig():
    """The scripted controller with an upload that stays in flight until the test says so."""
    controller = ScriptedController()
    upload = BlockingUpload()
    follow_ups = followup.FollowUps(controller, upload, lambda: "user-1")
    yield controller, follow_ups, upload
    upload.release.set()
    follow_ups.close()


def test_one_message_carries_every_screenshot_of_the_run(rig):
    controller, follow_ups, uploads, _who = rig
    controller.begin_run()
    reply = follow_ups.add(PNG, "3D view")
    assert "next message" in reply and "3D view" in reply
    follow_ups.add(PNG, "Red slice view")
    time.sleep(0.05)
    assert controller.sent == []  # the run is still going
    controller.end_run()
    _wait(lambda: controller.sent)
    assert controller.sent == [(
        "(Sent by SlicerFlow) The screenshots capture_view took: 3D view, Red slice view.",
        ["3d-view-1.png", "red-slice-view-2.png"],
    )]
    assert uploads == ["3d-view-1.png", "red-slice-view-2.png"]


def test_at_most_three_per_run(rig):
    controller, follow_ups, _uploads, _who = rig
    controller.begin_run()
    for _ in range(followup.MAX_PER_RUN):
        follow_ups.add(PNG, "3D view")
    with pytest.raises(ValueError, match="At most 3"):
        follow_ups.add(PNG, "3D view")


def test_no_capture_during_the_follow_up_run(rig):
    controller, follow_ups, _uploads, _who = rig
    controller.begin_run()
    follow_ups.add(PNG, "3D view")
    controller.end_run()
    _wait(lambda: controller.sent)  # the follow-up's own run is going now
    with pytest.raises(ValueError, match="looking at the screenshots already"):
        follow_ups.add(PNG, "3D view")
    controller.end_run()
    _wait(lambda: not follow_ups._in_follow_up)
    controller.begin_run()
    follow_ups.add(PNG, "3D view")  # a later reply may take new ones


@pytest.mark.parametrize("how", ["stop", "error", "account"])
def test_not_sent_after_stop_error_or_another_account(rig, how):
    controller, follow_ups, uploads, who = rig
    controller.begin_run()
    follow_ups.add(PNG, "3D view")
    if how == "stop":
        follow_ups.drop()
        controller.end_run()
    elif how == "error":
        controller.end_run(error="Something failed.")
    else:
        who["id"] = "user-2"
        controller.end_run()
    _wait(lambda: not follow_ups._delivering)
    assert controller.sent == [] and uploads == []


def test_waits_while_an_approval_is_pending(rig):
    controller, follow_ups, _uploads, _who = rig
    controller.begin_run()
    follow_ups.add(PNG, "3D view")
    controller.end_run(approval={"toolCallId": "tc-1"})
    time.sleep(3 * followup.APPROVAL_POLL_S)
    assert controller.sent == []
    controller.begin_run()  # the user approved: the resume run
    controller.end_run()
    _wait(lambda: controller.sent)


def test_a_busy_controller_drops_the_batch_without_breaking(rig):
    controller, follow_ups, _uploads, _who = rig
    controller.begin_run()
    follow_ups.add(PNG, "3D view")
    controller.busy = True
    controller.end_run()
    _wait(lambda: not follow_ups._delivering)
    assert controller.sent == []
    controller.busy = False
    controller.begin_run()
    follow_ups.add(PNG, "3D view")
    controller.end_run()
    _wait(lambda: controller.sent)


def test_close_ends_a_waiting_delivery(rig):
    controller, follow_ups, _uploads, _who = rig
    controller.begin_run()
    follow_ups.add(PNG, "3D view")
    follow_ups.close()
    controller.end_run()
    _wait(lambda: not follow_ups._delivering)
    assert controller.sent == []
    with pytest.raises(ValueError, match="closing"):
        follow_ups.add(PNG, "3D view")


def test_the_controller_tells_its_stop_listeners_first():
    t2v = Talk2View("pk_test_followup", base_url="http://127.0.0.1:9")
    controller = followup.FollowUpController(t2v, executor=lambda job: None)
    heard = []
    controller.add_stop_listener(lambda: heard.append("stop"))
    try:
        controller.cancel()
        controller.clear()
        assert heard == ["stop", "stop"]
    finally:
        controller.close()
        t2v.close()


@pytest.mark.parametrize("acts", ["stop", "new chat", "account"])
def test_a_batch_never_outlives_the_conversation_it_came_from(acts):
    """Uploading takes as long as the images take, and the user can act in that time. A
    screenshot must never land in the conversation that replaced the one it was taken in:
    it is the previous conversation's picture of the previous patient's scene.

    The real ``FollowUpController`` is driven here, wired as ``Runtime.start()`` wires it,
    so Stop and New chat are pressed rather than simulated. Its own executor runs, as in
    Slicer: nothing listens on the engine's port, so a turn and a session delete both end
    quickly, and the controller really is idle again by the time the upload finishes. A
    controller left busy would refuse the send for a reason that has nothing to do with the
    rule under test.
    """
    t2v = Talk2View("pk_test_followup", base_url="http://127.0.0.1:9")
    controller = followup.FollowUpController(t2v)
    upload = BlockingUpload()
    who = {"id": "user-1"}
    follow_ups = followup.FollowUps(controller, upload, lambda: who["id"])
    controller.add_stop_listener(follow_ups.drop)  # what Runtime.start() connects
    try:
        follow_ups.add(PNG, "3D view")
        assert upload.started.wait(5), "the upload never started"
        if acts == "stop":
            controller.cancel()
        elif acts == "new chat":
            controller.clear()
            _wait(lambda: not controller.snapshot()["isLoading"])  # the new chat is ready
        else:
            who["id"] = "user-2"  # signed out, or someone else signed in
        upload.release.set()
        # Either the batch was discarded (the delivery is over) or it was posted. Both
        # happen at once, so neither outcome is waited for longer than the other.
        _wait(lambda: not follow_ups._delivering or controller.snapshot()["messages"])
        assert controller.snapshot()["messages"] == []
    finally:
        upload.release.set()
        follow_ups.close()
        controller.close()
        t2v.close()


def test_an_upload_nobody_interrupted_is_delivered_exactly_once(held_rig):
    controller, follow_ups, upload = held_rig
    controller.begin_run()
    follow_ups.add(PNG, "3D view")
    controller.end_run()
    assert upload.started.wait(5), "the upload never started"
    upload.release.set()
    _wait(lambda: controller.sent)
    controller.end_run()  # the follow-up's own run
    _wait(lambda: not follow_ups._delivering)  # nothing more can be sent after this
    assert controller.sent == [(
        "(Sent by SlicerFlow) The screenshots capture_view took: 3D view.",
        ["3d-view-1.png"],
    )]


def test_a_screenshot_taken_while_the_batch_is_sending_is_delivered_too(held_rig):
    """The deliverer takes the batch and starts uploading; a last tool call of the run then
    captures another view. That one belongs to no batch in flight, so the thread that
    finishes must start a second delivery rather than strand it."""
    controller, follow_ups, upload = held_rig
    controller.begin_run()
    follow_ups.add(PNG, "3D view")
    controller.end_run()
    assert upload.started.wait(5), "the upload never started"
    follow_ups.add(PNG, "Red slice view")  # while the first is still on its way
    upload.release.set()
    _wait(lambda: controller.sent)
    controller.end_run()  # the first batch's own run ends
    _wait(lambda: len(controller.sent) == 2)
    assert [text for text, _files in controller.sent] == [
        "(Sent by SlicerFlow) The screenshots capture_view took: 3D view.",
        "(Sent by SlicerFlow) The screenshots capture_view took: Red slice view.",
    ]
    controller.end_run()


def test_a_re_sign_in_as_the_same_person_does_not_get_the_old_screenshots(held_rig):
    """Signing out forgets the conversation; signing back in as the same person leaves the
    controller's early return to keep the fresh one. The user id is unchanged either side,
    so only the auth event itself can tell the batch its conversation is gone."""
    controller, follow_ups, upload = held_rig
    controller.begin_run()
    follow_ups.add(PNG, "3D view")
    controller.end_run()
    assert upload.started.wait(5), "the upload never started"
    follow_ups.on_account_change(None)  # signed out
    follow_ups.on_account_change({"id": "user-1"})  # and straight back in, same account
    upload.release.set()
    _wait(lambda: not follow_ups._delivering)
    assert controller.sent == []


def test_the_check_and_the_send_are_one_step(held_rig):
    """The check that decides to send and the send itself hold one lock, so a ``drop()``
    cannot land between them. Nothing else can show the gap is gone: any test of the gap
    is a race, and winning it once proves nothing."""
    controller, follow_ups, upload = held_rig
    lock_held = []
    real_send = controller.send

    def watched_send(text, attachments=None):
        free = follow_ups._lock.acquire(blocking=False)
        if free:
            follow_ups._lock.release()
        lock_held.append(not free)
        real_send(text, attachments)

    controller.send = watched_send
    controller.begin_run()
    follow_ups.add(PNG, "3D view")
    controller.end_run()
    assert upload.started.wait(5), "the upload never started"
    upload.release.set()
    _wait(lambda: controller.sent)
    assert lock_held == [True], "send() ran with the lock released: a drop could land there"
    controller.end_run()


def test_a_chat_closed_while_it_uploads_is_logged_quietly(caplog):
    """Stop chat closes the SDK client one closer after this queue, and the delivery thread
    is never joined, so an upload in flight dies with it. httpx raises a plain
    ``RuntimeError`` for that, not a ``T2VError``, and an ordinary Stop must not look like
    a crash in the log."""
    controller = ScriptedController()
    started, go = threading.Event(), threading.Event()

    def dying_upload(png, filename):
        started.set()
        go.wait(5)
        raise RuntimeError("Cannot send a request, as the client has been closed.")

    follow_ups = followup.FollowUps(controller, dying_upload, lambda: "user-1")
    with caplog.at_level(logging.INFO, logger="SlicerFlow"):
        controller.begin_run()
        follow_ups.add(PNG, "3D view")
        controller.end_run()
        assert started.wait(5), "the upload never started"
        follow_ups.close()  # Stop chat
        go.set()
        _wait(lambda: not follow_ups._delivering)
    assert [r.getMessage() for r in caplog.records if r.exc_info] == []
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR] == []
    assert caplog.records, "the closed chat was not logged at all"


def test_an_upload_that_times_out_after_a_stop_is_logged_quietly(caplog):
    """Stop chat closes the SDK client, but that does not interrupt an upload another
    thread is already reading: it ends at the upload timeout instead, as a ``T2VError``.
    That branch used to log a warning, so an ordinary Stop left "the screenshots were not
    sent" in the log half a minute after the user pressed it."""
    controller = ScriptedController()
    started, go = threading.Event(), threading.Event()

    def timing_out_upload(png, filename):
        started.set()
        go.wait(5)
        raise T2VError("Network request failed: timed out", "network_error")

    follow_ups = followup.FollowUps(controller, timing_out_upload, lambda: "user-1")
    with caplog.at_level(logging.INFO, logger="SlicerFlow"):
        controller.begin_run()
        follow_ups.add(PNG, "3D view")
        controller.end_run()
        assert started.wait(5), "the upload never started"
        follow_ups.close()  # Stop chat
        go.set()
        _wait(lambda: not follow_ups._delivering)
    assert [r.levelname for r in caplog.records if r.levelno >= logging.WARNING] == []
    assert caplog.records, "the closed chat was not logged at all"


def test_a_later_reply_does_not_reuse_a_screenshot_filename(rig):
    """Two replies that each capture the 3D view: numbered within the batch, both would be
    ``3d-view-1.png``, so the model sees two different images under one name."""
    controller, follow_ups, uploads, _who = rig
    for reply in (1, 2):
        controller.begin_run()
        follow_ups.add(PNG, "3D view")
        controller.end_run()
        _wait(lambda: len(controller.sent) == reply)
        controller.end_run()  # the follow-up's own run ends
        _wait(lambda: not follow_ups._delivering)
    assert uploads == ["3d-view-1.png", "3d-view-2.png"]


def test_a_real_failure_is_still_logged_with_its_traceback(caplog):
    """Quietening the close must not quieten a genuine bug."""
    controller = ScriptedController()

    def broken_upload(png, filename):
        raise RuntimeError("something nobody expected went wrong")

    follow_ups = followup.FollowUps(controller, broken_upload, lambda: "user-1")
    try:
        with caplog.at_level(logging.INFO, logger="SlicerFlow"):
            controller.begin_run()
            follow_ups.add(PNG, "3D view")
            controller.end_run()
            _wait(lambda: not follow_ups._delivering)
        assert [r.levelname for r in caplog.records if r.exc_info] == ["ERROR"]
    finally:
        follow_ups.close()


def test_a_lost_batch_is_logged_as_a_warning(rig, caplog):
    """Screenshots the user asked for that never reach the model are worth more than a
    note in the log. The model is told nothing: an extra message could cascade."""
    controller, follow_ups, _uploads, _who = rig
    controller.begin_run()
    follow_ups.add(PNG, "3D view")
    controller.busy = True
    with caplog.at_level(logging.INFO, logger="SlicerFlow"):
        controller.end_run()
        _wait(lambda: not follow_ups._delivering)
    lost = [r.levelname for r in caplog.records if "not sent" in r.getMessage()]
    assert lost == ["WARNING"], [r.getMessage() for r in caplog.records]


def test_screenshots_taken_while_signed_out_are_never_sent():
    controller = ScriptedController()
    uploads = []

    def upload(png, filename):
        uploads.append(filename)
        return Attachment(id="att", filename=filename, mime_type="image/png",
                          size_bytes=len(png))

    follow_ups = followup.FollowUps(controller, upload, lambda: None)  # nobody is signed in
    try:
        controller.begin_run()
        follow_ups.add(PNG, "3D view")
        controller.end_run()
        _wait(lambda: not follow_ups._delivering)
        assert controller.sent == [] and uploads == []
    finally:
        follow_ups.close()


def test_the_run_an_approval_resumes_is_waited_for_too(rig):
    controller, follow_ups, _uploads, _who = rig
    controller.begin_run()
    follow_ups.add(PNG, "3D view")
    controller.end_run(approval={"toolCallId": "tc-1"})
    controller.resume_run()  # approved: idle is set while the resume run is still going
    time.sleep(0.05)
    assert controller.sent == [], "the follow-up landed in the middle of the resume run"
    controller.end_run()
    _wait(lambda: controller.sent)


def test_each_screenshot_in_a_batch_gets_its_own_filename(rig):
    controller, follow_ups, uploads, _who = rig
    controller.begin_run()
    follow_ups.add(PNG, "3D view")
    follow_ups.add(PNG, "3D view")  # the same view twice in one reply
    controller.end_run()
    _wait(lambda: controller.sent)
    assert len(set(uploads)) == 2, uploads


def test_a_sign_in_file_that_cannot_be_read_fails_closed(caplog):
    controller = ScriptedController()
    upload = BlockingUpload()
    readable = {"ok": True}

    def identity():
        if not readable["ok"]:
            raise OSError("auth.json cannot be read")
        return "user-1"

    follow_ups = followup.FollowUps(controller, upload, identity)
    try:
        with caplog.at_level(logging.INFO, logger="SlicerFlow"):
            controller.begin_run()
            follow_ups.add(PNG, "3D view")
            controller.end_run()
            assert upload.started.wait(5), "the upload never started"
            readable["ok"] = False  # the sign-in file went away while it uploaded
            upload.release.set()
            _wait(lambda: not follow_ups._delivering)
        assert controller.sent == []
        assert [r.getMessage() for r in caplog.records if r.exc_info] == [], \
            "it raised under the lock instead of failing closed"
    finally:
        upload.release.set()
        follow_ups.close()


def test_a_thread_switch_through_threadsstate_drops_the_queued_screenshots_like_new_chat_does():
    """An earlier version of this test mocked ``ChatController.
    restore_conversation`` and proved only that ``FollowUpController``'s own override
    called ``super()`` -- not that a real switch ever reaches it. ``ThreadsState`` (the
    thread list's actual production code, imported here rather than faked) switches with
    ``exchange_conversation``, which that override never caught: a follow-up's screenshots
    landed in the thread the user had just switched to. The fix is the SDK's own
    ``conversationReplaced`` notice, which fires for both ``restore_conversation`` and
    ``exchange_conversation`` (``ChatController.on()``), and ``FollowUpController``
    subscribes to it once in ``__init__`` instead of overriding either method.

    Drives the real ``ThreadsState`` over a real ``ChatController``/``Talk2View`` (no
    engine reached: every call here is local bookkeeping)."""
    from talk2view import MemoryStorage
    from talk2view.ui.hub import EventHub
    from talk2view.ui.threads import ThreadsState

    t2v = Talk2View("pk_test_followup", base_url="http://127.0.0.1:9", storage=MemoryStorage())
    try:
        controller = followup.FollowUpController(t2v)
        dropped = []
        controller.add_stop_listener(lambda: dropped.append(True))

        def message(n, text):
            return {
                "id": "msg_{}_1".format(n), "role": "user", "content": text,
                "timestamp": "2026-01-01T00:00:00.000Z",
            }

        # A transcript on screen before the list ever attaches: `attach()`'s own adoption
        # of it is export-only (no restore), so it must not count as a drop.
        controller.restore_conversation(
            {"messages": [message(1, "first chat")], "history": [{"role": "user", "content": "first chat"}], "threadId": None}
        )
        dropped.clear()

        threads = ThreadsState(controller, t2v)
        threads.attach(EventHub())
        try:
            assert dropped == []  # attach()'s own adoption: nothing replaced
            first_id = threads._hub.snapshot()["state"]["threads"]["currentId"]

            # Two per switch since R3's exchange_conversation override: its own early
            # notify, ahead of the swap, and conversationReplaced's own, after it. Both
            # reach the same idempotent FollowUps.drop() -- harmless, and R3's own test
            # (below) is what actually pins the ordering, not the count.
            threads.new_thread()  # New chat, with the list on -- I2's own repro
            assert dropped == [True, True]
            assert threads._hub.snapshot()["state"]["threads"]["currentId"] != first_id

            dropped.clear()
            controller.restore_conversation(
                {"messages": [message(2, "second chat")], "history": [{"role": "user", "content": "second chat"}], "threadId": None}
            )
            assert dropped == [True]  # restore_conversation has no early-notify override
            dropped.clear()  # isolate the switch below

            threads.switch(first_id)  # a thread switch -- the exact scenario I2 reproduced
            assert dropped == [True, True]

            dropped.clear()
            controller.clear()  # newSession's old path: still drops, as it always did
            assert dropped == [True]
        finally:
            threads.close()
    finally:
        controller.close()
        t2v.close()


def test_exchange_conversation_notifies_before_the_swap_not_the_conversationreplaced_notice_after_it():
    """``conversationReplaced`` is queued inside
    ``_apply_restore_locked`` (under the controller's lock) and delivered by the flush
    ``_after_restore`` makes once that lock is released -- AFTER the swap. A follow-up
    whose ``_still_ours`` check passes in that exact instant would have its ``send()``
    queued behind the swap and land in the thread just switched TO. The
    ``exchange_conversation`` override tells the stop listeners FIRST, before delegating:
    the first call a stop listener ever sees for one exchange must still find the OLD
    conversation on screen, not the new one."""
    from talk2view import MemoryStorage

    t2v = Talk2View("pk_test_followup_r3", base_url="http://127.0.0.1:9", storage=MemoryStorage())
    try:
        controller = followup.FollowUpController(t2v)

        def message(n, text):
            return {
                "id": "msg_{}_1".format(n), "role": "user", "content": text,
                "timestamp": "2026-01-01T00:00:00.000Z",
            }

        controller.restore_conversation(
            {"messages": [message(1, "old chat")], "history": [{"role": "user", "content": "old chat"}], "threadId": None}
        )

        seen_at_notify = []
        controller.add_stop_listener(
            lambda: seen_at_notify.append([m["content"] for m in controller.snapshot()["messages"]])
        )

        baseline, _ = controller.follow_identity(lambda change: None)
        controller.exchange_conversation(
            {"messages": [message(2, "new chat")], "history": [{"role": "user", "content": "new chat"}], "threadId": None},
            epoch=baseline.epoch,
        )

        # Two calls reach the listener for this one exchange: the override's, ahead of the
        # swap, and conversationReplaced's own, after it -- both are harmless (FollowUps.
        # drop() is idempotent), but the FIRST is what R3 is about.
        assert seen_at_notify[0] == ["old chat"]
        assert seen_at_notify[-1] == ["new chat"]

        # An export-only call replaces nothing: no early notify from the override, and
        # none from the notice either (the SDK itself does not raise one).
        seen_at_notify.clear()
        controller.exchange_conversation(None, epoch=baseline.epoch)
        assert seen_at_notify == []
    finally:
        controller.close()
        t2v.close()
