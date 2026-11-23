"""Run SlicerFlow's tests inside 3D Slicer, then exit with their verdict.

Only 3D Slicer runs this file, never a plain python:

    Slicer --testing --no-splash \\
      --additional-module-paths <repo>/SlicerFlow \\
      --python-script <repo>/SlicerFlow/Testing/Python/run_slicer_tests.py

It runs every ``test_*.py`` next to it (or the files matching ``SF_TEST_PATTERN``) with
``unittest``, on Slicer's main thread, with a main window. First it installs the Talk2View
SDK from PyPI when Start chat would (``sdk.needs_install()``: missing, or another
version); ``SF_SKIP_SDK_INSTALL=1`` turns that off.

Settings come from the environment. Never pass arguments: the Slicer launcher takes a
positional ``*.py`` argument for the script to run, in place of this one.

    SF_TEST_OUT        output directory (default: <repo>/build/slicer-tests)
    SF_TEST_PATTERN    which test files to run (default: test_*.py), e.g. test_tools_*.py
    SF_TEST_TIMEOUT_S  the watchdog's limit for the whole run (default: 900)

Outputs in ``SF_TEST_OUT``: ``result.json`` (``ok``, ``testsRun``, ``failures``,
``errors``, ``skipped``, ``slicer``, ``python``, ``elapsedS``), ``tests.log`` (the unittest
report) and, only when the run hung, ``hang-traceback.txt``.

Exit codes: 0 every test passed, 1 a test failed or errored, 4 the runner itself raised,
5 the watchdog fired.

Every test sees ``SLICERFLOW_HOME`` pointing at a fresh temporary directory, so no test
reads or writes the real sign-in, skills or lock file (``--testing`` gives Slicer
temporary settings, but in the same directory as the real ones).
"""

import faulthandler
import json
import os
import sys
import tempfile
import threading
import time
import traceback
import unittest

import slicer

HERE = os.path.dirname(os.path.abspath(globals().get("__file__") or sys.argv[0]))
MODULE_DIR = os.path.dirname(os.path.dirname(HERE))  # <repo>/SlicerFlow
REPO = os.path.dirname(MODULE_DIR)
OUT = os.path.abspath(os.environ.get("SF_TEST_OUT") or os.path.join(REPO, "build", "slicer-tests"))
TIMEOUT_S = float(os.environ.get("SF_TEST_TIMEOUT_S", "900"))
PATTERN = os.environ.get("SF_TEST_PATTERN") or "test_*.py"
STARTED = time.monotonic()


class _Tee:
    """unittest's stream: the console and tests.log."""

    def __init__(self, *streams):
        self._streams = streams

    def write(self, text):
        for stream in self._streams:
            stream.write(text)

    def flush(self):
        for stream in self._streams:
            stream.flush()


def _write_result(result, code, note=""):
    record = {
        "ok": code == 0,
        "exitCode": code,
        "pattern": PATTERN,
        "testsRun": getattr(result, "testsRun", 0),
        "failures": len(getattr(result, "failures", [])),
        "errors": len(getattr(result, "errors", [])),
        "skipped": len(getattr(result, "skipped", [])),
        "slicer": "{} r{}".format(slicer.app.applicationVersion, slicer.app.revision),
        "python": sys.version.split()[0],
        "elapsedS": round(time.monotonic() - STARTED, 1),
        "note": note,
    }
    with open(os.path.join(OUT, "result.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)


def _watchdog(hang_file):
    # A plain thread: it fires even when the Qt loop is wedged and no timer ticks again.
    time.sleep(TIMEOUT_S)
    faulthandler.dump_traceback(file=hang_file, all_threads=True)
    hang_file.flush()
    os._exit(5)


def _ensure_sdk():
    if os.environ.get("SF_SKIP_SDK_INSTALL") == "1":
        return
    if MODULE_DIR not in sys.path:
        sys.path.insert(0, MODULE_DIR)
    from SlicerFlowLib import sdk

    if sdk.needs_install():
        print("[sf-tests] installing the Talk2View SDK " + sdk.SDK_VERSION + " from PyPI")
        sdk.install(force=sdk.installed())


def main():
    os.makedirs(OUT, exist_ok=True)
    for stale in ("result.json", "tests.log", "hang-traceback.txt"):
        path = os.path.join(OUT, stale)
        if os.path.exists(path):
            os.remove(path)
    os.environ["SLICERFLOW_HOME"] = tempfile.mkdtemp(prefix="sf-home-")
    hang_file = open(os.path.join(OUT, "hang-traceback.txt"), "w", encoding="utf-8")
    threading.Thread(target=_watchdog, args=(hang_file,), name="sf-watchdog", daemon=True).start()
    if slicer.util.mainWindow() is None:
        raise RuntimeError("the tests need a main window: do not pass --no-main-window")
    _ensure_sdk()
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    suite = unittest.defaultTestLoader.discover(HERE, pattern=PATTERN, top_level_dir=HERE)
    with open(os.path.join(OUT, "tests.log"), "w", encoding="utf-8") as log:
        runner = unittest.TextTestRunner(stream=_Tee(sys.stdout, log), verbosity=2)
        result = runner.run(suite)
    code = 0 if result.wasSuccessful() and result.testsRun > 0 else 1
    note = "" if result.testsRun else "no test matched " + PATTERN
    _write_result(result, code, note)
    hang_file.close()
    os.remove(hang_file.name)
    return code


try:
    exit_code = main()
except Exception:
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "tests.log"), "a", encoding="utf-8") as fh:
        fh.write(traceback.format_exc())
    _write_result(None, 4, traceback.format_exc()[-2000:])
    exit_code = 4
slicer.util.exit(exit_code)
