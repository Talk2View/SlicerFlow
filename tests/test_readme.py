"""The README's tool table lists exactly the registered tools (read from the source)."""

import ast
import os
import re

from conftest import FREEZE_WARNING, REPO, tools_that_ask_first


def _tool_names():
    from conftest import TOOLS_DIR
    from SlicerFlowLib import user_tools

    return [tool for info in user_tools.read_builtin(TOOLS_DIR) for tool in info.tools]


def _readme():
    with open(os.path.join(REPO, "README.md"), encoding="utf-8") as fh:
        return fh.read()


def test_the_tool_table_matches_tools():
    table = re.findall(r"^\| `(\w+)`", _readme(), re.MULTILINE)
    assert table == _tool_names()


def test_the_asks_first_markers_name_the_tools_that_ask():
    """The table's "(asks first)" is a promise about approval, so it is checked against the
    tools that really wait for one; a tool whose approval was dropped must not keep the
    marker, and one that gained an approval must not be missing it."""
    marked = set(re.findall(r"^\| `(\w+)` \(asks first\)", _readme(), re.MULTILINE))
    assert marked == tools_that_ask_first()


def test_the_readme_says_a_sample_download_freezes_slicer():
    assert FREEZE_WARNING in _readme()


def test_the_readme_says_every_tool_that_asks_asks_every_time():
    """There is no "always allow" in SlicerFlow, and a reader should not have to press the
    button that is not there to find that out."""
    assert "asks every time" in _readme()


def test_the_readme_names_the_pinned_sdk_release():
    from SlicerFlowLib import sdk

    assert sdk.REQUIREMENT in _readme()


def test_the_readme_documents_your_own_tools():
    readme = _readme()
    assert "## Your own tools" in readme
    assert "permission=False" in readme  # the only way a user tool skips the approval
    assert "full access to your scene and your files" in readme
    assert "REQUIRES" in readme


def test_the_readme_does_not_promise_that_no_tool_runs_code():
    """User tool packs are Python: the "no tool runs Python code" promise is SlicerFlow's
    own tools' alone."""
    assert "No SlicerFlow tool runs Python code" in _readme()


def test_the_readme_says_the_builtin_packs_can_be_switched_off():
    readme = _readme()
    assert "switch any of them off under **Tools**" in readme
    for title in ("Scene & data", "Views", "Volumes", "Segmentation", "Markups", "Screenshots"):
        assert title in readme, title
    assert "**Your tools**" not in readme  # the section is called Tools now


def test_the_readme_says_slicerflows_packs_live_in_the_tools_folder():
    readme = _readme()
    assert "slicerflow_scene.py" in readme
    assert "**Edited**" in readme
    assert "**Restore original**" in readme
    assert "**Open in editor**" in readme
    assert ".py.bak" in readme
