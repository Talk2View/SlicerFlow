"""Helpers for SlicerFlow's in-Slicer tests (``run_slicer_tests.py`` runs them).

Importing this module points ``SLICERFLOW_HOME`` at a temporary directory unless the
runner already did, so a test started some other way (the Python console, "Reload and
Test") never touches the real sign-in or skills either.

Tests may pump Qt with ``wait_until``. Tool handlers never do: ``slicer.app.processEvents()``
inside a handler re-enters the main-thread queue the handler was posted through.
"""

import os
import tempfile
import time
import unittest

os.environ.setdefault("SLICERFLOW_HOME", tempfile.mkdtemp(prefix="sf-home-"))

import numpy as np  # noqa: E402
import slicer  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_DIR = os.path.dirname(os.path.dirname(HERE))  # <repo>/SlicerFlow
#: Nothing listens here, so a runtime's tool registration fails at once and is skipped.
UNREACHABLE_ENGINE = "http://127.0.0.1:9"


def wait_until(predicate, timeout_s=10.0, what="the condition"):
    """Pump Qt until ``predicate()`` is true; fail after ``timeout_s``."""
    deadline = time.monotonic() + timeout_s
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out after {} s waiting for {}".format(timeout_s, what))
        slicer.app.processEvents()
        time.sleep(0.01)


def make_volume(name="TestVolume", spacing=(1.0, 2.0, 0.5)):
    """A 40 x 30 x 20 int16 volume (i, j, k), 0 everywhere except a 20 x 10 x 10 block of
    100: 2000 voxels, 2.0 cm3 at the default spacing. Shown in the slice views."""
    array = np.zeros((20, 30, 40), dtype=np.int16)  # numpy order: k, j, i
    array[5:15, 10:20, 10:30] = 100
    node = slicer.util.addVolumeFromArray(array, name=name)
    node.SetSpacing(*spacing)
    node.CreateDefaultDisplayNodes()
    slicer.util.setSliceViewerLayers(background=node, fit=True)
    return node


class SceneTestCase(unittest.TestCase):
    """A clean scene and the conventional layout, shown, before every test."""

    def setUp(self):
        slicer.mrmlScene.Clear(0)
        layout = slicer.app.layoutManager()
        layout.setLayout(slicer.vtkMRMLLayoutNode.SlicerLayoutConventionalView)
        # setLayout() rebuilds the views; they are shown once the event loop has run.
        wait_until(
            lambda: layout.sliceWidget("Red").isVisible() and layout.threeDWidget(0).isVisible(),
            what="the conventional layout to be shown",
        )
