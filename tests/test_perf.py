"""``SlicerFlowLib.perf``: the metrics log, the main-thread measures and the panel's text,
with real turn timings from the bundled Talk2View SDK."""

import json
import os
import threading

from talk2view import TurnMetrics

from SlicerFlowLib import perf


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def _timing():
    """A real ``TurnTiming``: one turn through the SDK's own recorder, with a tool call."""
    metrics = TurnMetrics()
    heard = []
    metrics.on_turn(heard.append)
    turn = metrics._start_turn("msg_1_1")
    turn.request_sent()
    turn.tool_interrupt("set_layout")
    turn.tool_dispatched()
    turn.wrap_handler(lambda: "done")()
    turn.begin_resume()
    turn.request_sent()
    turn.finish("done")
    return heard[0]


def test_each_turn_is_one_json_line_with_slicer_s_part(tmp_path):
    log = perf.MetricsLog(str(tmp_path / "home" / "metrics.jsonl"))
    performance = perf.Performance(log, perf.PumpMonitor(20))
    timing = _timing()
    performance.on_turn(timing)
    performance.on_turn(timing)
    with open(log.path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    assert len(lines) == 2
    record = json.loads(lines[0])
    assert record["v"] == perf.FORMAT
    assert record["turn_id"] == "msg_1_1" and record["outcome"] == "done"
    assert record["tools"][0]["name"] == "set_layout"
    assert set(record["slicer"]) == {
        "posts", "max_queue_wait_ms", "total_queue_wait_ms", "stalls", "longest_stall_ms",
    }
    assert performance.version == 2 and len(performance.recent()) == 2


def test_the_log_rotates_and_keeps_one_old_file(tmp_path):
    log = perf.MetricsLog(str(tmp_path / "metrics.jsonl"), max_bytes=200)
    for i in range(20):
        assert log.write({"i": i, "pad": "x" * 40})
    names = sorted(os.listdir(tmp_path))
    assert names == ["metrics.jsonl", "metrics.jsonl.1"]
    assert os.path.getsize(log.path) <= 200
    with open(log.path + ".1", encoding="utf-8") as fh:
        old = [json.loads(line)["i"] for line in fh]
    with open(log.path, encoding="utf-8") as fh:
        new = [json.loads(line)["i"] for line in fh]
    assert new[-1] == 19 and old[-1] + 1 == new[0]


def test_a_log_that_cannot_be_written_never_raises(tmp_path, caplog):
    blocked = tmp_path / "metrics.jsonl"
    blocked.mkdir()  # a folder where the file should be
    log = perf.MetricsLog(str(blocked))
    assert log.write({"a": 1}) is False
    assert log.write({"a": 2}) is False
    assert caplog.text.count("could not write") == 1
    performance = perf.Performance(log)
    performance.on_turn(_timing())  # the panel still hears of it
    assert performance.version == 1


def test_a_timing_that_cannot_be_read_is_skipped(tmp_path):
    performance = perf.Performance(perf.MetricsLog(str(tmp_path / "m.jsonl")))
    performance.on_turn(object())
    assert performance.version == 0 and not os.path.exists(tmp_path / "m.jsonl")


def test_the_pump_monitor_finds_queue_waits_and_stalls_in_a_turn_s_window():
    clock = Clock()
    pump = perf.PumpMonitor(20, stall_ms=200, clock=clock)
    pump.tick()
    clock.now += 0.02
    pump.tick()  # on time
    stamp = pump.posted()
    clock.now += 0.015
    pump.ran(stamp)  # waited 15 ms in the queue
    clock.now += 0.5
    pump.tick()  # 515 ms since the last tick: the main thread was busy
    clock.now += 0.02
    pump.tick()
    summary = pump.summary(100.0, clock.now)
    assert summary == {
        "posts": 1,
        "max_queue_wait_ms": 15.0,
        "total_queue_wait_ms": 15.0,
        "stalls": 1,
        "longest_stall_ms": 515.0,
    }
    assert pump.summary(200.0, 300.0) == {
        "posts": 0, "max_queue_wait_ms": None, "total_queue_wait_ms": 0.0,
        "stalls": 0, "longest_stall_ms": None,
    }


def test_posted_work_that_runs_long_is_a_stall_at_once_and_only_once():
    clock = Clock()
    pump = perf.PumpMonitor(20, stall_ms=200, clock=clock)
    pump.tick()
    stamp = pump.posted()
    clock.now += 0.001
    started = pump.ran(stamp)  # the timer runs a tool handler...
    clock.now += 0.4
    pump.done(started)  # ...for 400 ms
    assert pump.summary(100.0, clock.now)["stalls"] == 1
    clock.now += 0.02
    pump.tick()  # the tick after it is on time again
    summary = pump.summary(100.0, clock.now)
    assert (summary["stalls"], summary["longest_stall_ms"]) == (1, 400.0)
    quick = pump.ran(pump.posted())
    clock.now += 0.05
    pump.done(quick)
    assert pump.summary(100.0, clock.now)["stalls"] == 1


def test_the_pump_monitor_keeps_a_bounded_history():
    clock = Clock()
    pump = perf.PumpMonitor(20, keep=8, clock=clock)
    for _ in range(100):
        pump.done(pump.ran(pump.posted()))
        clock.now += 1.0
        pump.tick()
    assert pump.summary(0, clock.now)["posts"] == 8
    assert pump.summary(0, clock.now)["stalls"] == 8


def test_the_performance_text_names_each_step():
    record = {
        "started_at": "2026-09-24T01:02:03.456Z",
        "outcome": "done",
        "total_ms": 8412.0,
        "time_to_first_text_ms": 3120.0,
        "engine_wait_ms": 2900.4,
        "setup_ms": 12.0,
        "tools": [
            {
                "name": "load_sample_data", "outcome": "ok", "total_ms": 4210.0,
                "main_thread_wait_ms": 18.0, "run_ms": 3900.0, "resume_ms": 290.0,
                "approval_wait_ms": None,
            },
            {
                "name": "capture_view", "outcome": "denied", "total_ms": 400.0,
                "main_thread_wait_ms": None, "run_ms": None, "resume_ms": 380.0,
                "approval_wait_ms": 5100.0,
            },
        ],
        "engine_tools": [],
        "ui": {"reported": True, "send_to_post_ms": 3.2, "delivery_ms": 11.0,
               "first_op_to_paint_ms": 16.4},
        "slicer": {"stalls": 1, "longest_stall_ms": 3900.0},
    }
    text = perf.format_turn(record)
    lines = text.splitlines()
    assert lines[0].endswith("  8.4 s")
    assert lines[1] == "  first words 3.1 s, engine/model wait 2.9 s"
    assert lines[2] == (
        "  load_sample_data: 4.2 s (waited for Slicer 18 ms, ran 3.9 s, resume 290 ms)"
    )
    assert lines[3] == "  capture_view: 400 ms (resume 380 ms, you took 5.1 s to answer, denied)"
    assert lines[4] == "  chat page: send 3 ms, to the page 11 ms, painted 16 ms"
    assert lines[5] == "  Slicer was busy 1 time (longest 3.9 s)"
    failed = dict(record, outcome="error", error_type="model_error", tools=[], ui=None, slicer=None)
    assert "(error: model_error)" in perf.format_turn(failed).splitlines()[0]
    assert perf.format_recent([]) == "No messages timed yet. Send one in the chat."
    assert perf.format_recent([record, record]).count("first words") == 2


def test_on_turn_from_many_threads_keeps_the_last_five(tmp_path):
    performance = perf.Performance(perf.MetricsLog(str(tmp_path / "m.jsonl")))
    timing = _timing()
    threads = [threading.Thread(target=performance.on_turn, args=(timing,)) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert performance.version == 12 and len(performance.recent()) == perf.KEEP
    with open(tmp_path / "m.jsonl", encoding="utf-8") as fh:
        assert len(fh.read().splitlines()) == 12


def test_the_engine_s_own_timing_is_one_line_with_its_largest_steps():
    record = {
        "outcome": "done", "total_ms": 12000, "time_to_first_text_ms": 9000, "engine_wait_ms": 8000,
        "request": {"server_timing": {"auth": 40, "session": 20, "prereqs": 6100, "agent_init": 2300,
                                       "untimed": 0.4, "total": 9280}},
    }
    text = perf.format_turn(record)
    assert "  engine before replying 9.3 s (prereqs 6.1 s, agent init 2.3 s, auth 40 ms)" in text


def test_no_engine_line_when_the_engine_sent_no_timing():
    record = {"outcome": "done", "total_ms": 900, "request": {"server_timing": None}}
    assert "engine before replying" not in perf.format_turn(record)

