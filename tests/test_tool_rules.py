"""Rules every tool module keeps, checked in the source (no Slicer needed)."""

import ast
import glob
import os

from conftest import FREEZE_WARNING, REPO, presentation, tool_functions

TOOL_FILES = sorted(glob.glob(os.path.join(REPO, "SlicerFlow", "SlicerFlowLib", "tools", "*.py")))


def _tree(path):
    with open(path, encoding="utf-8") as fh:
        return ast.parse(fh.read(), path)


def test_there_are_tool_modules():
    assert len(TOOL_FILES) >= 5


def test_no_tool_pumps_the_qt_event_loop():
    for path in TOOL_FILES:
        for node in ast.walk(_tree(path)):
            assert not (isinstance(node, ast.Attribute) and node.attr == "processEvents"), path


def test_load_sample_data_says_that_slicer_stops_responding():
    """The download runs on Slicer's main thread, and no tool pumps the event loop, so the
    window is frozen for the whole of it: no repaint, no progress, and Stop cannot even be
    clicked. By the tool's own description that can be a minute, which users read as a
    crash. Both the user (the approval card) and the model (the description) are told.
    """
    assert FREEZE_WARNING in presentation()["load_sample_data"]["warning"]
    assert FREEZE_WARNING in ast.get_docstring(tool_functions()["load_sample_data"])


def test_no_tool_module_defers_its_annotations():
    # @tool builds the schema from real annotations; postponed ones would be strings.
    for path in TOOL_FILES:
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                assert "annotations" not in [alias.name for alias in node.names], path


def test_no_tool_module_imports_relatively():
    """SlicerFlow writes its packs into the user's tools folder, and an edited copy there
    is run by path, outside the package, where a relative import fails. The packs import
    SlicerFlowLib.tools instead. (The package's own __init__ is no pack, and stays.)"""
    for path in TOOL_FILES:
        if os.path.basename(path) == "__init__.py":
            continue
        for node in ast.walk(_tree(path)):
            assert not (isinstance(node, ast.ImportFrom) and node.level), path
