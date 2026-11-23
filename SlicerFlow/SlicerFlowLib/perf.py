"""Where the chat's time goes: one line per message in ``metrics.jsonl``, and the text the
module panel's Performance section shows.

The Talk2View SDK times every turn (``t2v.metrics``, a ``TurnTiming``: the engine's wait,
the first words, each tool's wait for Slicer's main thread, its run and its resume). This
module adds what only Slicer can see, the main thread itself (``PumpMonitor``): how long
work posted to it waited in the queue the chat's 20 ms timer drains, and every gap of more
than ``STALL_MS`` between two ticks of that timer, when Slicer was too busy to run it.

``Performance.on_turn`` gets each turn from the SDK on the thread that ended it (never
under a chat lock), appends one JSON line to ``<home>/metrics.jsonl`` there, and keeps the
last ``KEEP`` turns for the panel. A failure to write is logged once and never reaches the
chat. The line holds timings, tool names, outcome and error types and counts only: the
SDK records no message, reply, argument, result, token or URL.

This module imports neither ``slicer`` nor ``qt``, so it runs without Slicer.
"""

import collections
import datetime
import json
import logging
import os
import threading
import time

#: The log rotates at this size; one older file (``metrics.jsonl.1``) is kept.
MAX_BYTES = 1000000
#: A gap between two main-thread timer ticks longer than this (plus the interval) is a stall.
STALL_MS = 200
#: How many turns the panel shows.
KEEP = 5
#: The line format's version (``v``).
FORMAT = 1

logger = logging.getLogger("SlicerFlow")


class MetricsLog:
    """``metrics.jsonl``: one JSON object per line, appended; at ``max_bytes`` the file
    becomes ``metrics.jsonl.1`` (replacing the older one) and a new file starts. Safe from
    any thread. ``write`` never raises."""

    def __init__(self, path, max_bytes=MAX_BYTES):
        self.path = path
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        self._warned = False

    def write(self, record):
        try:
            line = json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"
            with self._lock:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                try:
                    size = os.path.getsize(self.path)
                except OSError:
                    size = 0
                if size and size + len(line) > self._max_bytes:
                    os.replace(self.path, self.path + ".1")
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line)
            return True
        except Exception as exc:  # the chat must never notice
            if not self._warned:
                self._warned = True
                logger.warning("SlicerFlow: could not write %s (%s)", self.path, exc)
            return False


class PumpMonitor:
    """Slicer's main-thread queue, measured. ``posted()`` when work is queued returns a
    stamp; ``ran(stamp)`` when the timer starts it returns another, for ``done(...)`` when
    it returns; ``tick()`` at the top of every timer callback. A stall is a gap between two
    ticks longer than the interval plus ``stall_ms``; posted work that ran that long is
    counted as one when it returns, so a turn that ends right after it still sees it. The
    last ``keep`` waits and stalls are kept, with ``time.monotonic()`` times, so
    ``summary()`` can answer for any turn's window. Cheap: a clock read and a deque append
    each."""

    def __init__(self, interval_ms, stall_ms=STALL_MS, keep=512, clock=time.monotonic):
        self._limit = (interval_ms + stall_ms) / 1000.0
        self._clock = clock
        self._last_tick = None
        self._waits = collections.deque(maxlen=keep)  # (ran at, waited s)
        self._stalls = collections.deque(maxlen=keep)  # (started at, lasted s)

    def posted(self):
        return self._clock()

    def ran(self, stamp):
        now = self._clock()
        self._waits.append((now, now - stamp))
        return now

    def done(self, started):
        now = self._clock()
        if now - started > self._limit:
            self._stalls.append((started, now - started))
            self._last_tick = now  # the next tick's gap starts here: never counted twice

    def tick(self):
        now = self._clock()
        last, self._last_tick = self._last_tick, now
        if last is not None and now - last > self._limit:
            self._stalls.append((last, now - last))

    def summary(self, start, end):
        """What the main thread did between two ``time.monotonic()`` times: how many posted
        callables ran, the longest and total queue wait, and the stalls that overlap."""
        waits = [w for at, w in list(self._waits) if start <= at <= end]
        stalls = [s for at, s in list(self._stalls) if at <= end and at + s >= start]
        return {
            "posts": len(waits),
            "max_queue_wait_ms": _ms(max(waits)) if waits else None,
            "total_queue_wait_ms": _ms(sum(waits)) if waits else 0.0,
            "stalls": len(stalls),
            "longest_stall_ms": _ms(max(stalls)) if stalls else None,
        }


class Performance:
    """The chat's turn timings: the log file, and the last ``keep`` turns for the panel
    (``recent()``, newest first; ``version`` goes up with every turn)."""

    def __init__(self, log, pump=None, keep=KEEP):
        self.log = log
        self._pump = pump
        self._lock = threading.Lock()
        self._recent = collections.deque(maxlen=keep)
        self.version = 0

    def on_turn(self, timing):
        """A ``talk2view.TurnTiming``; called by the SDK. Never raises."""
        try:
            record = {"v": FORMAT}
            record.update(timing.as_dict())
            if self._pump is not None:
                start = timing.started_monotonic
                record["slicer"] = self._pump.summary(start, start + timing.total_ms / 1000.0)
        except Exception:
            logger.exception("SlicerFlow: could not read a turn timing")
            return
        self.log.write(record)
        with self._lock:
            self._recent.appendleft(record)
            self.version += 1

    def recent(self):
        with self._lock:
            return list(self._recent)

    def text(self):
        return format_recent(self.recent())


# ── The panel's text ─────────────────────────────────────────────────────────


def _ms(seconds):
    return round(seconds * 1000.0, 1)


def duration(ms):
    """``850 ms``, ``3.4 s``, or ``?`` when it was not measured."""
    if ms is None:
        return "?"
    if ms < 1000:
        return "{} ms".format(int(round(ms)))
    return "{:.1f} s".format(ms / 1000.0)


def _clock_time(started_at):
    try:
        moment = datetime.datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%S.%fZ")
        moment = moment.replace(tzinfo=datetime.timezone.utc).astimezone()
        return moment.strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return "--:--:--"


def _tool_line(tool):
    parts = []
    if tool.get("main_thread_wait_ms") is not None:
        parts.append("waited for Slicer {}".format(duration(tool["main_thread_wait_ms"])))
    if tool.get("run_ms") is not None:
        parts.append("ran {}".format(duration(tool["run_ms"])))
    if tool.get("resume_ms") is not None:
        parts.append("resume {}".format(duration(tool["resume_ms"])))
    if tool.get("approval_wait_ms"):
        parts.append("you took {} to answer".format(duration(tool["approval_wait_ms"])))
    outcome = tool.get("outcome")
    if outcome not in ("ok", None):
        parts.append(outcome.replace("_", " "))
    line = "  {}: {}".format(tool.get("name", "?"), duration(tool.get("total_ms")))
    return line + (" ({})".format(", ".join(parts)) if parts else "")


def _server_line(request):
    """The engine's own account of the time before it answered (its Server-Timing header):
    the total and the three largest steps, or None when the engine sent none."""
    phases = (request or {}).get("server_timing") or {}
    if not isinstance(phases, dict) or not phases:
        return None
    total = phases.get("total")
    steps = sorted(
        ((name, ms) for name, ms in phases.items() if name != "total" and isinstance(ms, (int, float))),
        key=lambda item: item[1], reverse=True,
    )[:3]
    shown = ", ".join("{} {}".format(name.replace("_", " "), duration(ms)) for name, ms in steps if ms >= 1)
    return "  engine before replying {}{}".format(
        duration(total), " ({})".format(shown) if shown else ""
    )


def format_turn(record):
    """One turn as a few plain lines for the panel."""
    outcome = record.get("outcome", "?")
    head = "{}  {}".format(_clock_time(record.get("started_at")), duration(record.get("total_ms")))
    if outcome != "done":
        head += "  ({}{})".format(
            outcome, ": " + record["error_type"] if record.get("error_type") else ""
        )
    lines = [head]
    lines.append(
        "  first words {}, engine/model wait {}".format(
            duration(record.get("time_to_first_text_ms")), duration(record.get("engine_wait_ms"))
        )
    )
    server = _server_line(record.get("request"))
    if server:
        lines.append(server)
    setup = record.get("setup_ms")
    if setup is not None and setup >= 300:
        lines.append("  before the request went out {}".format(duration(setup)))
    lines.extend(_tool_line(tool) for tool in record.get("tools") or [])
    for tool in record.get("engine_tools") or []:
        lines.append("  {} (on the server): {}".format(tool.get("name", "?"), duration(tool.get("duration_ms"))))
    ui = record.get("ui") or {}
    if ui.get("reported"):
        lines.append(
            "  chat page: send {}, to the page {}, painted {}".format(
                duration(ui.get("send_to_post_ms")),
                duration(ui.get("delivery_ms")),
                duration(ui.get("first_op_to_paint_ms")),
            )
        )
    slicer = record.get("slicer") or {}
    if slicer.get("stalls"):
        lines.append(
            "  Slicer was busy {} time{} (longest {})".format(
                slicer["stalls"], "" if slicer["stalls"] == 1 else "s",
                duration(slicer.get("longest_stall_ms")),
            )
        )
    return "\n".join(lines)


def format_recent(records):
    if not records:
        return "No messages timed yet. Send one in the chat."
    return "\n\n".join(format_turn(r) for r in records)
