"""The chat list (the SDK's threads slice) survives stopping and starting the chat: the
transcript is kept in the sign-in file's storage and put back on the next start, in a
file of its own -- never in auth.json, which is rewritten on every refresh (the reason
for ``Talk2View(data_storage=...)`` in ``runtime.py``).

One test method: a second test method reusing the same ``SLICERFLOW_HOME`` and the same
fake engine's one signed-in identity would see the first test's real, still-on-disk
conversation as "the newest stored conversation" at start -- correctly, now that
``talk2view.storage.FileStorage``'s cache is validated against the file's stat: an
``os.remove()`` between methods would separate them.
One flow needs no such cleanup step at all, so it stays that way.
"""

import json
import os
import unittest
import urllib.request
import uuid

import sf_testing  # noqa: F401  (isolates SLICERFLOW_HOME first)

from fake_engine import DONE, STOP, FakeEngine, text
from sf_testing import wait_until
from SlicerFlowLib import lifecycle, paths


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.engine = FakeEngine().start()
        self.addCleanup(self.engine.stop)
        self.addCleanup(lifecycle.stop)

    def _start(self):
        from SlicerFlowLib.runtime import Runtime

        runtime = Runtime("pk_test_history", self.engine.url, lambda url: None)
        lifecycle.start(runtime)
        return runtime

    def _command(self, runtime, type_, **fields):
        body = dict(fields, id=str(uuid.uuid4()), type=type_)
        token = runtime.server.url.split("#t=", 1)[1]
        request = urllib.request.Request(
            "http://127.0.0.1:{}/command".format(runtime.server.port),
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 202)

    def _threads(self, runtime):
        return runtime.server._hub.snapshot()["state"].get("threads")

    def test_the_list_and_the_transcript_come_back_after_a_restart(self):
        self.engine.script([text("Hello from the engine."), STOP, DONE])
        runtime = self._start()
        self._command(runtime, "login", email="user@example.com", password="secret")
        wait_until(lambda: runtime.t2v.auth.is_authenticated(), timeout_s=20, what="the sign-in")
        self._command(runtime, "send", text="Remember this one", attachmentIds=[])
        wait_until(lambda: not runtime.controller.snapshot()["isLoading"]
                   and len(runtime.controller.snapshot()["messages"]) == 2, timeout_s=20, what="the reply")
        current = self._threads(runtime)["currentId"]
        wait_until(lambda: self._threads(runtime)["items"][0]["messageCount"] == 2, timeout_s=10,
                   what="the list to see the reply")
        self.assertEqual(self._threads(runtime)["items"][0]["title"], "Remember this one")
        lifecycle.stop()  # ThreadsState.close() writes the pending copy

        # The chat went into its own file, never into the sign-in one.
        self.assertTrue(os.path.exists(paths.conversations_file()))
        with open(paths.conversations_file(), encoding="utf-8") as f:
            conversations_raw = f.read()
        self.assertIn("Remember this one", conversations_raw)
        self.assertTrue(os.path.exists(paths.token_file()))
        with open(paths.token_file(), encoding="utf-8") as f:
            auth_raw = f.read()
        self.assertNotIn("Remember this one", auth_raw)
        self.assertNotIn("t2v_chat_conversations_v1", auth_raw)

        again = self._start()
        wait_until(lambda: again.t2v.auth.is_authenticated(), timeout_s=20, what="the kept sign-in")
        wait_until(lambda: self._threads(again) is not None and self._threads(again)["currentId"] == current,
                   timeout_s=20, what="the list to come back")
        self.assertEqual([m["content"] for m in again.controller.snapshot()["messages"]],
                         ["Remember this one", "Hello from the engine."])
        self.assertEqual(self._threads(again)["items"][0]["id"], current)
        # New chat keeps it and detaches: no DELETE reached the engine.
        self._command(again, "threadNew")
        wait_until(lambda: self._threads(again)["currentId"] != current, timeout_s=10, what="a new chat")
        self.assertFalse(any(method == "DELETE" for method, _path, _body in self.engine.requests))
