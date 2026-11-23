"""The user's tool packs (``SlicerFlowLib.user_tools``), without 3D Slicer: the files, the
on/off state, the template, and loading."""

import ast
import json
import os
import sys
import textwrap
import types

import pytest

from SlicerFlowLib import user_tools


def write(folder, name, source):
    os.makedirs(str(folder), exist_ok=True)
    path = os.path.join(str(folder), name + ".py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent(source))
    return path


def test_scan_lists_py_files_in_name_order_without_private_ones(tmp_path):
    for name in ("b_pack.py", "a_pack.py", "_helper.py", ".hidden.py", "notes.txt"):
        (tmp_path / name).write_text("")
    (tmp_path / "folder.py").mkdir()
    assert user_tools.scan(str(tmp_path)) == ["a_pack", "b_pack"]


def test_scan_of_a_missing_folder_is_empty(tmp_path):
    assert user_tools.scan(str(tmp_path / "nope")) == []


def test_the_on_off_state_round_trips_both_lists(tmp_path):
    path = str(tmp_path / "home" / "tools.json")
    assert user_tools.read_state(path) == user_tools.ToolState()
    user_tools.write_state(path, user_tools.ToolState(enabled={"b", "a"}, builtin_off={"capture"}))
    assert user_tools.read_state(path) == user_tools.ToolState({"a", "b"}, {"capture"})
    with open(path, encoding="utf-8") as fh:
        assert json.load(fh) == {"enabled": ["a", "b"], "builtin_off": ["capture"], "builtin_written": {}}
    assert not os.path.exists(path + ".tmp")


def test_a_tools_json_from_before_builtin_packs_keeps_every_builtin_on(tmp_path):
    path = tmp_path / "tools.json"
    path.write_text('{"enabled": ["greet"]}')
    assert user_tools.read_state(str(path)) == user_tools.ToolState({"greet"}, set())


@pytest.mark.parametrize("content, expected", [
    ("not json", user_tools.ToolState()),
    ('{"enabled": "greet", "builtin_off": "view"}', user_tools.ToolState()),
    ("[1, 2]", user_tools.ToolState()),
    ('{"enabled": [1, "greet", null], "builtin_off": ["view", 2, "no_such_pack"]}',
     user_tools.ToolState({"greet"}, {"view", "no_such_pack"})),
])
def test_a_broken_tools_json_turns_user_packs_off_and_never_raises(tmp_path, content, expected):
    path = tmp_path / "tools.json"
    path.write_text(content)
    assert user_tools.read_state(str(path)) == expected


def test_the_folder_readme_is_written_and_kept_current(tmp_path):
    folder = user_tools.ensure_user_dir(str(tmp_path / "tools"))
    readme = os.path.join(folder, "README.txt")
    text = open(readme, encoding="utf-8").read()
    assert "TOOLS = [...]" in text and "permission=False" in text and "full access" in text
    assert "slicerflow_" in text and "Restore original" in text
    # An older SlicerFlow's text (or anything else) is replaced: the file is SlicerFlow's.
    with open(readme, "w", encoding="utf-8") as fh:
        fh.write("an older README")
    user_tools.ensure_user_dir(folder)
    assert open(readme, encoding="utf-8").read() == user_tools.USER_README


def test_a_folder_that_refuses_the_readme_is_still_the_tools_folder(tmp_path):
    """The chat starts through here, and must start whatever the folder does."""
    (tmp_path / "README.txt").mkdir()  # a directory where the file should be
    assert user_tools.ensure_user_dir(str(tmp_path)) == str(tmp_path)
    assert not (tmp_path / "README.txt.tmp").exists()  # no half-written leftovers


def test_new_pack_writes_the_template_named_after_the_pack(tmp_path):
    path = user_tools.new_pack(str(tmp_path / "tools"), "lung")
    assert path == str(tmp_path / "tools" / "lung.py")
    source = open(path, encoding="utf-8").read()
    assert "def lung_count_volumes" in source
    assert "TOOLS = [lung_count_volumes]" in source
    assert "{name}" not in source
    tree = ast.parse(source)
    assert not any(
        isinstance(node, ast.ImportFrom) and node.module == "__future__" for node in tree.body
    )


@pytest.mark.parametrize("name", ["", "Lung", "1lung", "lung-tools", "lung.py", "x" * 41])
def test_new_pack_refuses_a_bad_name(tmp_path, name):
    with pytest.raises(ValueError):
        user_tools.new_pack(str(tmp_path), name)


def test_new_pack_refuses_a_name_that_is_taken(tmp_path):
    user_tools.new_pack(str(tmp_path), "lung")
    with pytest.raises(FileExistsError):
        user_tools.new_pack(str(tmp_path), "lung")


def test_import_pack_copies_a_py_file_and_overwrites_only_when_asked(tmp_path):
    source = write(tmp_path / "downloads", "Lung_Tools", "TOOLS = []\n")
    folder = str(tmp_path / "tools")
    assert user_tools.import_pack(folder, source) == "Lung_Tools"
    assert open(user_tools.pack_path(folder, "Lung_Tools")).read() == "TOOLS = []\n"
    with pytest.raises(FileExistsError):
        user_tools.import_pack(folder, source)
    write(tmp_path / "downloads", "Lung_Tools", "TOOLS = None\n")
    user_tools.import_pack(folder, source, replace=True)
    assert open(user_tools.pack_path(folder, "Lung_Tools")).read() == "TOOLS = None\n"


@pytest.mark.parametrize("filename", ["notes.txt", "_private.py", ".hidden.py"])
def test_import_pack_refuses_what_scan_would_not_list(tmp_path, filename):
    source = tmp_path / filename
    source.write_text("")
    with pytest.raises(ValueError):
        user_tools.import_pack(str(tmp_path / "tools"), str(source))


GOOD = '''
    from talk2view import tool


    @tool
    def say_hi(name: str) -> str:
        """Greet someone.

        Args:
            name: Who to greet.
        """
        return "hi " + name


    @tool(permission=False)
    def count_things() -> str:
        """Count the things."""
        return "3"


    TOOLS = [say_hi, count_things]
    PRESENTATION = {"say_hi": {"activity": "Greeting"}}
'''

#: Dedented here, not by ``write``: the cases below append unindented lines to it, and
#: ``textwrap.dedent`` finds no common indent once they are there.
ONE_TOOL = textwrap.dedent('''
    from talk2view import tool


    @tool
    def f() -> str:
        """Do f."""
        return "f"


''')


def test_a_good_pack_loads_and_its_tools_ask_first_unless_they_say_not(tmp_path):
    path = write(tmp_path, "greet", GOOD)
    pack = user_tools.load_pack("greet", path, {})
    assert pack.error is None and pack.missing == {}
    assert [tool.name for tool in pack.tools] == ["say_hi", "count_things"]
    assert [tool._t2v_permission for tool in pack.tools] == [True, False]
    assert pack.presentation == {"say_hi": {"activity": "Greeting"}}
    assert pack.tools[0](name="Ann") == "hi Ann"


@pytest.mark.parametrize("source, expected", [
    ("raise RuntimeError('boom')\n", "RuntimeError: boom"),
    ("import sys\nsys.exit(3)\n", "SystemExit: 3"),
    ("def broken(:\n    pass\n", "SyntaxError on line 1"),
    ("from talk2view import tool\n", "No TOOLS list"),
    ("def plain():\n    'Doc.'\n\nTOOLS = [plain]\n", "'plain' in TOOLS is not decorated with @tool"),
    (ONE_TOOL.replace('"""Do f."""', "") + "TOOLS = [f]\n", "f has no docstring"),
    ("from __future__ import annotations\n" + ONE_TOOL + "TOOLS = [f]\n",
     "Remove 'from __future__ import annotations'"),
    (ONE_TOOL + "TOOLS = [f, f]\n", "f is in TOOLS twice"),
    (ONE_TOOL + "TOOLS = [f]\nPRESENTATION = {'f': {'warning': 'careful'}}\n",
     "PRESENTATION for f: a warning only shows for a destructive tool"),
    (ONE_TOOL + "TOOLS = [f]\nPRESENTATION = {'g': {'activity': 'x'}}\n",
     "PRESENTATION names g, which is not in TOOLS"),
    ("REQUIRES = ['numpy']\n", "REQUIRES must be a dict"),
    ("REQUIRES = {'x': '--index-url=https://example.com/simple'}\n",
     "REQUIRES names pip packages only"),
    ("REQUIRES = {'x': 'scikit-image --pre'}\n", "REQUIRES names pip packages only"),
    (ONE_TOOL.replace("@tool", '@tool(permission="ask")') + "TOOLS = [f]\n",
     "f has permission='ask'"),
])
def test_a_broken_pack_says_why_and_has_no_tools(tmp_path, source, expected):
    pack = user_tools.load_pack("broken", write(tmp_path, "broken", source), {})
    assert expected in pack.error
    assert pack.tools == [] and pack.presentation == {}


def test_a_tool_name_that_is_taken_fails_the_pack_and_says_by_whom(tmp_path):
    pack = user_tools.load_pack("greet", write(tmp_path, "greet", GOOD), {"say_hi": "SlicerFlow"})
    assert pack.error == "say_hi is already a tool of SlicerFlow: rename yours."
    assert pack.tools == []


def test_packs_load_in_name_order_and_the_later_of_two_clashing_packs_fails(tmp_path):
    write(tmp_path, "b_pack", GOOD)
    write(tmp_path, "a_pack", GOOD)
    packs = user_tools.load_packs(str(tmp_path), {"a_pack", "b_pack"}, {})
    assert [pack.name for pack in packs] == ["a_pack", "b_pack"]
    assert packs[0].error is None
    assert packs[1].error == "say_hi is already a tool of the tool pack a_pack: rename yours."


def test_only_packs_that_are_on_run_and_a_broken_or_vanished_one_stops_nothing(tmp_path):
    write(tmp_path, "explodes", "raise RuntimeError('boom')\n")
    write(tmp_path, "off", "raise AssertionError('a pack that is off ran')\n")
    write(tmp_path, "greet", GOOD)
    packs = user_tools.load_packs(str(tmp_path), {"explodes", "greet", "deleted"}, {})
    assert [(pack.name, pack.error is None) for pack in packs] == [("explodes", False),
                                                                    ("greet", True)]


def test_a_pack_missing_a_package_says_so_and_is_not_run(tmp_path):
    path = write(tmp_path, "needs", '''
        import not_a_real_package_sf
        REQUIRES = {"not_a_real_package_sf": "not-a-real-package", "json": "json"}
        raise AssertionError("a pack missing a package ran")
    ''')
    pack = user_tools.load_pack("needs", path, {})
    assert pack.missing == {"not_a_real_package_sf": "not-a-real-package"}
    assert pack.error is None and pack.tools == []


def test_a_missing_parent_package_counts_as_missing():
    assert user_tools.missing_packages({"no_such_parent_sf.child": "x"}) == {
        "no_such_parent_sf.child": "x"}


def test_an_edited_pack_loads_its_new_code_and_leaves_no_pycache(tmp_path):
    path = write(tmp_path, "greet", GOOD)
    assert user_tools.load_pack("greet", path, {}).tools[0].description == "Greet someone."
    write(tmp_path, "greet", GOOD.replace("Greet someone.", "Greet anyone."))
    assert user_tools.load_pack("greet", path, {}).tools[0].description == "Greet anyone."
    assert not (tmp_path / "__pycache__").exists()


def test_check_requires_reports_without_running_the_pack(tmp_path):
    path = write(tmp_path, "p", "REQUIRES = {'not_a_real_package_sf': 'nope'}\nraise SystemExit\n")
    assert user_tools.check_requires(path) == ({"not_a_real_package_sf": "nope"}, None)
    bad = write(tmp_path, "q", "def broken(:\n")
    missing, problem = user_tools.check_requires(bad)
    assert missing == {} and problem.startswith("SyntaxError on line 1")


def _loaded(tmp_path):
    return user_tools.load_pack("greet", write(tmp_path, "greet", GOOD), {})


def test_pack_status_says_what_to_expect_of_a_pack_and_how_to_show_it(tmp_path):
    status = user_tools.pack_status
    assert status(True, {}, problem="SyntaxError on line 1: x") == (
        "error", "Error: SyntaxError on line 1: x")
    assert status(True, {"skimage": "scikit-image", "a": "b"}) == ("needs", "Needs b, scikit-image")
    assert status(False, {}) == ("off", "Off")
    assert status(True, {}) == ("pending", "Loads on next start")
    failed = user_tools.Pack("p", "p.py", error="RuntimeError: boom")
    assert status(True, {}, failed) == ("error", "Error: RuntimeError: boom")
    assert status(True, {}, _loaded(tmp_path)) == ("on", "On · 2 tools")


def test_pack_status_counts_tools_in_plain_words(tmp_path):
    """Which tools ask first is for the panel's tool list, not the Status column."""
    one = user_tools.load_pack("one", write(tmp_path, "one", ONE_TOOL + "TOOLS = [f]\n"), {})
    assert user_tools.pack_status(True, {}, one) == ("on", "On · 1 tool")
    quiet = ONE_TOOL.replace("@tool", "@tool(permission=False)") + "TOOLS = [f]\n"
    silent = user_tools.load_pack("quiet", write(tmp_path, "quiet", quiet), {})
    assert user_tools.pack_status(True, {}, silent) == ("on", "On · 1 tool")


def _fake_module(source):
    module = types.ModuleType("fake_builtin")
    exec(textwrap.dedent(source), module.__dict__)
    return module


FAKE_BUILTIN = '''
    from talk2view import tool

    TITLE = "Fake tools"


    @tool
    def look() -> str:
        """Look at something."""
        return "ok"


    @tool(permission=True)
    def change() -> str:
        """Change something."""
        return "ok"


    TOOLS = [look, change]
    PRESENTATION = {"change": {"activity": "Changing"}}
'''


def test_a_builtin_pack_keeps_each_tools_own_permission():
    pack = user_tools.builtin_pack("fake", _fake_module(FAKE_BUILTIN), {})
    assert pack.error is None and pack.builtin and pack.title == "Fake tools"
    # A built-in tool that does not ask must never start asking: only user packs ask by default.
    assert [(tool.name, tool._t2v_permission) for tool in pack.tools] == [("look", None), ("change", True)]
    assert pack.presentation == {"change": {"activity": "Changing"}}


def test_a_builtin_pack_is_held_to_the_pack_rules():
    pack = user_tools.builtin_pack("fake", _fake_module("TITLE = 'X'\nTOOLS = []\n"), {})
    assert "No TOOLS list" in pack.error and pack.tools == []
    taken = user_tools.builtin_pack("fake", _fake_module(FAKE_BUILTIN), {"look": "SlicerFlow"})
    assert taken.error == "look is already a tool of SlicerFlow: rename yours."


def test_a_user_pack_is_not_builtin_and_is_titled_by_its_name():
    pack = user_tools.Pack("greet", "greet.py")
    assert not pack.builtin and pack.title == "greet"


def test_the_state_round_trips_what_slicerflow_wrote(tmp_path):
    path = str(tmp_path / "tools.json")
    state = user_tools.ToolState(written={"scene": "abc"})
    user_tools.write_state(path, state)
    assert user_tools.read_state(path) == state
    with open(path, encoding="utf-8") as fh:
        assert json.load(fh)["builtin_written"] == {"scene": "abc"}
    junk = tmp_path / "junk.json"
    junk.write_text('{"builtin_written": ["scene"]}')
    assert user_tools.read_state(str(junk)).written == {}
    junk.write_text('{"builtin_written": {"scene": 1, "view": "def"}}')
    assert user_tools.read_state(str(junk)).written == {"view": "def"}


SOURCES = {
    "scene": "# SlicerFlow's scene pack, version 1\n",
    "view": "# SlicerFlow's view pack, version 1\n",
}


def _text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_sync_writes_the_missing_builtin_files_and_records_what_it_wrote(tmp_path):
    state = user_tools.ToolState()
    files, changed = user_tools.sync_builtins(str(tmp_path), state, SOURCES)
    assert changed
    assert [(f.name, f.stem, f.edited, f.newer) for f in files] == [
        ("scene", "slicerflow_scene", False, False), ("view", "slicerflow_view", False, False)]
    assert files[0].path == str(tmp_path / "slicerflow_scene.py")
    assert _text(files[0].path) == SOURCES["scene"]
    assert state.written == {name: user_tools.digest(text) for name, text in SOURCES.items()}
    assert user_tools.scan(str(tmp_path)) == ["slicerflow_scene", "slicerflow_view"]
    assert not os.path.exists(files[0].path + ".tmp")
    # Up to date: nothing more to write or record.
    assert user_tools.sync_builtins(str(tmp_path), state, SOURCES) == (files, False)


def test_sync_updates_a_file_slicerflow_wrote_when_its_source_changed(tmp_path):
    state = user_tools.ToolState()
    user_tools.sync_builtins(str(tmp_path), state, SOURCES)
    newer = dict(SOURCES, view="# SlicerFlow's view pack, version 2\n")
    files, changed = user_tools.sync_builtins(str(tmp_path), state, newer)
    assert changed and not files[1].edited
    assert _text(files[1].path) == newer["view"]
    assert state.written["view"] == user_tools.digest(newer["view"])


def test_sync_never_touches_an_edited_file_and_says_when_slicerflow_has_a_newer_version(tmp_path):
    state = user_tools.ToolState()
    user_tools.sync_builtins(str(tmp_path), state, SOURCES)
    path = str(tmp_path / "slicerflow_view.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# my version\n")
    files, changed = user_tools.sync_builtins(str(tmp_path), state, SOURCES)
    assert not changed
    assert (files[1].edited, files[1].newer) == (True, False)
    assert _text(path) == "# my version\n"
    newer = dict(SOURCES, view="# SlicerFlow's view pack, version 2\n")
    files, changed = user_tools.sync_builtins(str(tmp_path), state, newer)
    assert not changed
    assert (files[1].edited, files[1].newer) == (True, True)
    assert _text(path) == "# my version\n"
    assert state.written["view"] == user_tools.digest(SOURCES["view"])  # what SlicerFlow last wrote


def test_sync_takes_a_file_it_has_no_record_of_as_edited_unless_it_matches(tmp_path):
    (tmp_path / "slicerflow_scene.py").write_text(SOURCES["scene"], encoding="utf-8")
    (tmp_path / "slicerflow_view.py").write_text("# the user's own slicerflow_view.py\n", encoding="utf-8")
    state = user_tools.ToolState()
    files, changed = user_tools.sync_builtins(str(tmp_path), state, SOURCES)
    assert changed
    assert [(f.edited, f.newer) for f in files] == [(False, False), (True, True)]
    assert state.written == {"scene": user_tools.digest(SOURCES["scene"])}
    assert _text(files[1].path) == "# the user's own slicerflow_view.py\n"


def test_sync_writes_a_deleted_builtin_file_again(tmp_path):
    state = user_tools.ToolState()
    files, _changed = user_tools.sync_builtins(str(tmp_path), state, SOURCES)
    os.remove(files[0].path)
    files, changed = user_tools.sync_builtins(str(tmp_path), state, SOURCES)
    assert not changed and not files[0].edited
    assert _text(files[0].path) == SOURCES["scene"]


def test_sync_takes_a_fresh_file_out_of_enabled(tmp_path):
    state = user_tools.ToolState(enabled={"slicerflow_scene", "mine"})
    _files, changed = user_tools.sync_builtins(str(tmp_path), state, SOURCES)
    assert changed and state.enabled == {"mine"}


def test_a_change_of_line_endings_alone_is_not_an_edit(tmp_path):
    state = user_tools.ToolState()
    files, _changed = user_tools.sync_builtins(str(tmp_path), state, SOURCES)
    with open(files[0].path, "wb") as fh:
        fh.write(SOURCES["scene"].replace("\n", "\r\n").encode("utf-8"))
    files, _changed = user_tools.sync_builtins(str(tmp_path), state, SOURCES)
    assert not files[0].edited


def test_a_byte_order_mark_alone_is_not_an_edit(tmp_path):
    """Some Windows editors save UTF-8 with a BOM in front."""
    state = user_tools.ToolState()
    files, _changed = user_tools.sync_builtins(str(tmp_path), state, SOURCES)
    with open(files[0].path, "wb") as fh:
        fh.write(b"\xef\xbb\xbf" + SOURCES["scene"].encode("utf-8"))
    files, _changed = user_tools.sync_builtins(str(tmp_path), state, SOURCES)
    assert not files[0].edited


def test_a_file_that_is_not_utf8_is_edited_and_left_alone(tmp_path):
    state = user_tools.ToolState()
    files, _changed = user_tools.sync_builtins(str(tmp_path), state, SOURCES)
    with open(files[0].path, "wb") as fh:
        fh.write(b"# saved as Latin-1: caf\xe9\n")
    files, changed = user_tools.sync_builtins(str(tmp_path), state, SOURCES)
    assert not changed and files[0].edited
    with open(files[0].path, "rb") as fh:
        assert fh.read() == b"# saved as Latin-1: caf\xe9\n"


def test_a_folder_that_refuses_a_read_is_skipped_not_fatal(tmp_path):
    (tmp_path / "slicerflow_scene.py").mkdir()  # a directory where the file should be
    state = user_tools.ToolState()
    files, changed = user_tools.sync_builtins(str(tmp_path), state, SOURCES)
    assert changed
    assert [(f.name, f.edited) for f in files] == [("scene", False), ("view", False)]
    assert "scene" not in state.written and "view" in state.written


def test_restore_puts_slicerflows_version_back_and_keeps_the_edit_as_bak(tmp_path):
    state = user_tools.ToolState(builtin_off={"view"})
    files, _changed = user_tools.sync_builtins(str(tmp_path), state, SOURCES)
    path = files[1].path
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# my version\n")
    state.enabled.add("slicerflow_view")
    assert user_tools.restore_builtin(str(tmp_path), state, "view", SOURCES["view"]) == path
    assert _text(path) == SOURCES["view"]
    assert _text(path + ".bak") == "# my version\n"
    assert state.enabled == set() and state.builtin_off == {"view"}  # on or off as before
    assert state.written["view"] == user_tools.digest(SOURCES["view"])
    files, changed = user_tools.sync_builtins(str(tmp_path), state, SOURCES)
    assert not changed and not files[1].edited
    assert user_tools.scan(str(tmp_path)) == ["slicerflow_scene", "slicerflow_view"]  # the .bak is no pack


def test_the_builtin_sources_and_order_come_from_the_tools_package():
    from conftest import TOOLS_DIR

    order = user_tools.builtin_order(TOOLS_DIR)
    assert order == ["scene", "view", "volume", "segmentation", "markups", "capture"]
    sources = user_tools.builtin_sources(TOOLS_DIR)
    assert list(sources) == order
    assert all('TITLE = "' in text for text in sources.values())


def test_read_pack_reads_what_the_source_declares_without_running_it(tmp_path):
    source = GOOD.replace("TOOLS = [say_hi, count_things]",
                          'TITLE = "Greetings"\n    raise SystemExit\n    TOOLS = [say_hi, count_things]')
    info = user_tools.read_pack(write(tmp_path, "greet", source))
    assert info.title == "Greetings"
    assert [(t.name, t.summary, t.permission) for t in info.tools] == [
        ("say_hi", "Greet someone.", None), ("count_things", "Count the things.", False)]
    assert info.presentation == {"say_hi": {"activity": "Greeting"}}


def test_read_pack_of_a_file_that_does_not_parse_or_says_nothing_plainly_is_empty(tmp_path):
    assert user_tools.read_pack(write(tmp_path, "broken", "def broken(:\n")) == user_tools.PackInfo()
    assert user_tools.read_pack(str(tmp_path / "missing.py")) == user_tools.PackInfo()
    odd = write(tmp_path, "odd", "TITLE = 3\nTOOLS = make()\nPRESENTATION = build()\n")
    assert user_tools.read_pack(odd) == user_tools.PackInfo()


def test_read_pack_summarises_a_wrapped_docstring_on_one_line(tmp_path):
    path = write(tmp_path, "p", '''
        from talk2view import tool


        @tool(permission=True)
        def f() -> str:
            """Do the first
            thing.

            Then more detail nobody lists.
            """
            return "f"


        TOOLS = [f, imported_elsewhere]
    ''')
    info = user_tools.read_pack(path)
    assert [(t.name, t.summary, t.permission) for t in info.tools] == [
        ("f", "Do the first thing.", True), ("imported_elsewhere", "", None)]


def test_new_pack_refuses_slicerflows_own_names(tmp_path):
    with pytest.raises(ValueError, match="SlicerFlow's own"):
        user_tools.new_pack(str(tmp_path), "slicerflow_scene")


@pytest.mark.parametrize("platform, expected", [
    ("darwin", ["open", "-t", "/t/p.py"]),
    ("linux", ["xdg-open", "/t/p.py"]),
])
def test_open_in_editor_asks_for_a_text_editor_never_a_run(platform, expected):
    ran = []
    user_tools.open_in_editor("/t/p.py", platform, run=ran.append)
    assert ran == [expected]


def test_open_in_editor_on_windows_uses_the_edit_verb_then_notepad():
    calls = []
    user_tools.open_in_editor("C:\\t\\p.py", "win32", run=calls.append,
                              startfile=lambda path, verb: calls.append(("startfile", path, verb)))
    assert calls == [("startfile", "C:\\t\\p.py", "edit")]

    def no_edit_verb(path, verb):
        raise OSError("no edit verb")

    calls = []
    user_tools.open_in_editor("C:\\t\\p.py", "win32", run=calls.append, startfile=no_edit_verb)
    assert calls == [["notepad", "C:\\t\\p.py"]]


@pytest.mark.parametrize("platform, expected", [
    ("darwin", ["open", "-R", "/t/p.py"]),
    ("win32", ["explorer", "/select,", "/t/p.py"]),
    ("linux", ["xdg-open", "/t"]),
])
def test_show_in_folder_selects_the_file_where_the_desktop_can(platform, expected):
    ran = []
    user_tools.show_in_folder("/t/p.py", platform, run=ran.append)
    assert ran == [expected]


def test_save_state_logs_a_tools_json_it_cannot_replace_and_goes_on(tmp_path, caplog):
    """The chat start and the panel save what a sync recorded; a tools.json that cannot be
    replaced (a directory, a read-only folder, a file another program holds) must stop
    neither. ``write_state`` still raises, for the actions that show the error."""
    path = tmp_path / "tools.json"
    path.mkdir()
    with caplog.at_level("WARNING", logger="SlicerFlow"):
        user_tools.save_state(str(path), user_tools.ToolState(enabled={"mine"}))
    assert "could not be saved" in caplog.text
    assert not (tmp_path / "tools.json.tmp").exists()
    with pytest.raises(OSError):
        user_tools.write_state(str(path), user_tools.ToolState())


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                    reason="needs a folder its owner cannot write to")
def test_a_folder_that_refuses_a_write_is_skipped_not_fatal(tmp_path, caplog):
    folder = tmp_path / "tools"
    folder.mkdir()
    folder.chmod(0o500)
    try:
        state = user_tools.ToolState(enabled={"slicerflow_view", "mine"})
        with caplog.at_level("WARNING", logger="SlicerFlow"):
            files, changed = user_tools.sync_builtins(str(folder), state, SOURCES)
        assert [(f.name, f.edited) for f in files] == [("scene", False), ("view", False)]
        assert state.written == {}  # nothing was written, so nothing is recorded
        assert changed and state.enabled == {"mine"}  # a fresh pack's stem still leaves
        assert [r.getMessage().split(" could not")[0] for r in caplog.records] == [
            "SlicerFlow: " + f.path for f in files]
        assert os.listdir(str(folder)) == []  # no .tmp left behind
    finally:
        folder.chmod(0o700)


def test_a_folder_that_keeps_refusing_is_logged_once(tmp_path, caplog):
    """The panel syncs on every refresh, which every chat status change calls."""
    (tmp_path / "slicerflow_scene.py").mkdir()  # a directory where the file should be
    with caplog.at_level("WARNING", logger="SlicerFlow"):
        for _ in range(3):
            user_tools.sync_builtins(str(tmp_path), user_tools.ToolState(), SOURCES)
            user_tools.save_state(str(tmp_path / "slicerflow_scene.py"), user_tools.ToolState())
    assert len(caplog.records) == 2  # one read, one save



def test_a_restore_that_cannot_write_leaves_the_edited_file_where_it_was(tmp_path):
    state = user_tools.ToolState()
    files, _changed = user_tools.sync_builtins(str(tmp_path), state, SOURCES)
    path = files[1].path
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# my version\n")
    state.enabled.add("slicerflow_view")
    os.mkdir(path + ".tmp")  # where SlicerFlow's version would be written first
    with pytest.raises(OSError):
        user_tools.restore_builtin(str(tmp_path), state, "view", SOURCES["view"])
    assert _text(path) == "# my version\n"
    assert not os.path.exists(path + ".bak")
    assert "slicerflow_view" in state.enabled  # nothing changed
