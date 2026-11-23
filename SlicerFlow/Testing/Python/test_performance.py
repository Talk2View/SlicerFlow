"""Turn timings inside Slicer: every message becomes one line in ``metrics.jsonl``, with
Slicer's own main-thread measures. (The module panel's Performance section that showed
the last ones is commented out for now, see SlicerFlow.py; its widget behaviour is not
tested here any more.)"""

import json
import os
import time
import unittest
import urllib.request
import uuid

import sf_testing  # noqa: F401  (isolates SLICERFLOW_HOME first)
import slicer

from fake_engine import DONE, STOP, FakeEngine, text, tool_call
from sf_testing import make_volume, wait_until
from SlicerFlowLib import lifecycle, paths

QUESTION = "Switch to four-up PRIVATE-QUESTION-3141"


class PerformanceTest(unittest.TestCase):
    def setUp(self):
        slicer.mrmlScene.Clear(0)
        make_volume("CT")
        slicer.app.layoutManager().setLayout(slicer.vtkMRMLLayoutNode.SlicerLayoutConventionalView)
        for path in (paths.metrics_file(), paths.metrics_file() + ".1"):
            if os.path.exists(path):
                os.remove(path)
        self.engine = FakeEngine().start()
        self.addCleanup(self.engine.stop)
        self.addCleanup(lifecycle.stop)
        from SlicerFlowLib.runtime import Runtime

        self.runtime = Runtime("pk_test_perf", self.engine.url, lambda url: None)
        lifecycle.start(self.runtime)

    def command(self, type_, **fields):
        body = dict(fields, id=str(uuid.uuid4()), type=type_)
        token = self.runtime.server.url.split("#t=", 1)[1]
        request = urllib.request.Request(
            "http://127.0.0.1:{}/command".format(self.runtime.server.port),
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 202)

    def lines(self):
        if not os.path.exists(paths.metrics_file()):
            return []
        with open(paths.metrics_file(), encoding="utf-8") as fh:
            return fh.read().splitlines()

    def test_a_message_with_a_tool_is_logged(self):
        self.engine.script(
            [tool_call("set_layout", "tc-1", {"layout": "four-up"})],
            [text("Four-up it is."), STOP, DONE],
        )
        self.command("login", email="user@example.com", password="secret")
        wait_until(lambda: self.runtime.t2v.auth.is_authenticated(), timeout_s=20,
                   what="the sign-in")
        self.command("send", text=QUESTION, attachmentIds=[])
        wait_until(lambda: self.lines(), timeout_s=20, what="the turn's line in metrics.jsonl")

        line = self.lines()[0]
        self.assertNotIn("PRIVATE-QUESTION", line)
        self.assertNotIn("Four-up it is", line)
        self.assertNotIn("four-up", line)  # the tool's arguments
        record = json.loads(line)
        self.assertEqual(record["outcome"], "done")
        (tool,) = record["tools"]
        self.assertEqual((tool["name"], tool["outcome"]), ("set_layout", "ok"))
        for key in ("main_thread_wait_ms", "run_ms", "resume_ms", "total_ms"):
            self.assertIsInstance(tool[key], float, key)
        self.assertIsInstance(record["time_to_first_text_ms"], float)
        self.assertIsInstance(record["engine_wait_ms"], float)
        self.assertGreaterEqual(record["slicer"]["posts"], 1)  # the handler ran through it
        self.assertIsNotNone(record["slicer"]["max_queue_wait_ms"])
        self.assertIsNotNone(record["ui"]["queue_ms"])

    def test_a_busy_main_thread_is_seen(self):
        pump = self.runtime.host.pump
        start = time.monotonic()
        ran = []
        self.runtime.host.post(lambda: (time.sleep(0.35), ran.append(True)))
        wait_until(lambda: ran, what="the slow main-thread job")
        wait_until(lambda: pump.summary(start, time.monotonic())["stalls"] >= 1,
                   what="the stall to be noticed on the next tick")
        summary = pump.summary(start, time.monotonic())
        self.assertGreaterEqual(summary["longest_stall_ms"], 300)
        self.assertGreaterEqual(summary["posts"], 1)
