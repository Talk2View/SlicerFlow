"""SlicerFlow's built-in tools are packs: one module each, read here from the source."""

import ast
import os

from conftest import TOOLS_DIR
from SlicerFlowLib import user_tools

EXPECTED = [
    ("scene", "Scene & data",
     ["get_scene_context", "list_sample_data", "load_sample_data", "load_file"],
     ["load_sample_data", "load_file"]),
    ("view", "Views", ["set_layout", "navigate_slice", "center_views"], []),
    ("volume", "Volumes", ["set_window_level", "set_volume_rendering"], []),
    ("segmentation", "Segmentation",
     ["set_segment_display", "threshold_segment", "measure_segment_volumes"], ["threshold_segment"]),
    ("markups", "Markups", ["add_markup_point"], []),
    ("capture", "Screenshots", ["capture_view"], ["capture_view"]),
]


def test_the_builtin_packs_are_read_from_their_source():
    infos = user_tools.read_builtin(TOOLS_DIR)
    assert [(i.name, i.title, i.tools, i.asking) for i in infos] == EXPECTED


def test_builtin_dir_is_the_tools_package():
    assert os.path.samefile(user_tools.BUILTIN_DIR, TOOLS_DIR)


def test_every_presentation_names_only_its_own_modules_tools():
    for name, _title, tools, _asking in EXPECTED:
        with open(os.path.join(TOOLS_DIR, name + ".py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        presentation = next(
            ast.literal_eval(node.value) for node in tree.body
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "PRESENTATION"
        )
        assert set(presentation) == set(tools), name


def test_builtin_status_says_how_many_tools():
    """Which of them ask first is for the panel's tool list, not the Status column."""
    status = user_tools.builtin_status
    infos = {name: user_tools.read_pack(os.path.join(TOOLS_DIR, name + ".py"))
             for name, _title, _tools, _asking in EXPECTED}
    assert status(True, infos["scene"]) == ("on", "On · 4 tools")
    assert status(True, infos["view"]) == ("on", "On · 3 tools")
    assert status(True, infos["capture"]) == ("on", "On · 1 tool")
    assert status(False, infos["scene"]) == ("off", "Off")


def test_every_builtin_tool_says_whether_it_asks_first():
    """SlicerFlow writes its packs into the user's tools folder, and an edited copy loads
    like a user's pack, where a tool that does not say asks first. Each built-in tool says,
    so an edited copy keeps the original's approvals."""
    for name, _title, tools, asking in EXPECTED:
        info = user_tools.read_pack(os.path.join(TOOLS_DIR, name + ".py"))
        assert [(tool.name, tool.permission) for tool in info.tools] == [
            (tool, tool in asking) for tool in tools], name
