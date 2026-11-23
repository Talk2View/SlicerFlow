"""The module panel's Tools section: one list of every pack, SlicerFlow's own (written into
the tools folder, kept current unless edited) first, then the user's; each with an on/off
box; the selected pack's tools and file; New, Import, Install, Restore original and the
restart bar."""

import os
import tempfile
import textwrap
import types
import unittest

import sf_testing  # noqa: F401  (isolates SLICERFLOW_HOME first)
import qt
import slicer

from sf_testing import UNREACHABLE_ENGINE, wait_until
from SlicerFlowLib import lifecycle, paths, tools_panel, user_tools

BUILTIN_ROWS = [
    ("Scene & data (SlicerFlow)", "On · 4 tools"),
    ("Views (SlicerFlow)", "On · 3 tools"),
    ("Volumes (SlicerFlow)", "On · 2 tools"),
    ("Segmentation (SlicerFlow)", "On · 3 tools"),
    ("Markups (SlicerFlow)", "On · 1 tool"),
    ("Screenshots (SlicerFlow)", "On · 1 tool"),
]
BUILTIN = len(BUILTIN_ROWS)
BUILTIN_NAMES = ["scene", "view", "volume", "segmentation", "markups", "capture"]

MINE = '''
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

#: A tool added to an edited copy of SlicerFlow's markups pack, saying nothing about asking.
ADDED_TOOL = '''@tool
def count_point_lists() -> str:
    """Count the point lists in the scene."""
    return "0"


'''


def write_pack(name, source="TOOLS = []\n"):
    folder = user_tools.ensure_user_dir(paths.user_tools_dir())
    path = user_tools.pack_path(folder, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent(source))
    return path


def write_state(enabled=(), builtin_off=()):
    state = user_tools.read_state(paths.user_tools_file())
    state.enabled = set(enabled)
    state.builtin_off = set(builtin_off)
    user_tools.write_state(paths.user_tools_file(), state)


def builtin_path(name):
    return user_tools.pack_path(paths.user_tools_dir(), "slicerflow_" + name)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class ToolsPanelTest(unittest.TestCase):
    def setUp(self):
        saved = os.environ.get("SLICERFLOW_HOME")
        os.environ["SLICERFLOW_HOME"] = tempfile.mkdtemp(prefix="sf-tools-panel-")
        self.addCleanup(
            lambda: os.environ.__setitem__("SLICERFLOW_HOME", saved) if saved is not None
            else os.environ.pop("SLICERFLOW_HOME", None)
        )
        self.restarts = []
        self.widget = qt.QWidget()
        self.section = tools_panel.ToolsSection(qt.QVBoxLayout(self.widget),
                                                restart=lambda: self.restarts.append(1))
        self.asked = []
        self.answer = True
        self.section._confirm = lambda text: self.asked.append(text) or self.answer
        self.opened = []
        self.section._open = self.opened.append
        self.edited = []
        self.section._openInEditor = self.edited.append
        self.shown = []
        self.section._showInFolder = self.shown.append

    def fake_runtime(self, *packs):
        """What the panel reads of a running chat (``Runtime.packs`` and ``.tools``)."""
        runtime = types.SimpleNamespace(packs=list(packs), tools=[])
        setattr(slicer.modules, lifecycle.RUNTIME_ATTR, runtime)
        self.addCleanup(setattr, slicer.modules, lifecycle.RUNTIME_ATTR, None)
        return runtime

    def builtin_packs(self, *off):
        return [user_tools.Pack(name, "", builtin=True) for name in BUILTIN_NAMES if name not in off]

    def item(self, row):
        return self.section.packTree.topLevelItem(row)

    def rows(self):
        return [(self.item(row).text(0), self.item(row).text(1))
                for row in range(self.section.packTree.topLevelItemCount)]

    def user_rows(self):
        return self.rows()[BUILTIN:]

    def test_every_description_is_shown_whole_at_any_width(self):
        self.widget.show()  # laid out, as in Slicer's panel
        self.addCleanup(self.widget.hide)
        self.section.box.collapsed = False
        self.section.refresh()
        self.section.packTree.setCurrentItem(self.item(0))  # Scene & data: load_file's is long
        tree = self.section.toolTree
        self.assertEqual(tree.textElideMode, qt.Qt.ElideNone)
        self.assertEqual(tree.horizontalScrollBarPolicy, qt.Qt.ScrollBarAlwaysOff)
        self.addCleanup(tree.setMaximumWidth, 16777215)
        self.addCleanup(tree.setMinimumWidth, 0)
        for width in (420, 260):
            tree.setFixedWidth(width)
            for _ in range(20):
                slicer.app.processEvents()
            column = tree.header().sectionSize(1)
            metrics = qt.QFontMetrics(tree.font)
            total = 0
            for i in range(tree.topLevelItemCount):
                item = tree.topLevelItem(i)
                text_width = column - 2 * (tree.style().pixelMetric(qt.QStyle.PM_FocusFrameHMargin) + 1)
                wrapped = metrics.boundingRect(qt.QRect(0, 0, text_width, 100000),
                                               qt.Qt.TextWordWrap, item.text(1)).height()
                self.assertGreaterEqual(tree.visualItemRect(item).height(), wrapped, (width, item.text(0)))
                total += tree.visualItemRect(item).height()
            # Every row in view: the list is as tall as all of them.
            self.assertGreaterEqual(tree.viewport().height, total, width)

    def tool_rows(self):
        tree = self.section.toolTree
        return [(tree.topLevelItem(i).text(0), tree.topLevelItem(i).text(1))
                for i in range(tree.topLevelItemCount)]

    def state(self):
        return user_tools.read_state(paths.user_tools_file())

    def edit_builtin(self, name, old, new):
        """Let a refresh write SlicerFlow's files, then change one of them."""
        self.section.refresh()
        path = builtin_path(name)
        source = read(path)
        self.assertIn(old, source)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(source.replace(old, new))
        return path

    # ── The list ──

    def test_the_builtin_packs_come_first_then_yours(self):
        write_pack("a_pack")
        write_pack("b_pack")
        write_state(enabled={"a_pack"})
        self.section.refresh()
        self.assertEqual(self.rows()[:BUILTIN], BUILTIN_ROWS)
        self.assertEqual(self.item(0).toolTip(0), builtin_path("scene"))
        self.assertEqual(self.user_rows(), [("a_pack (Yours)", "Loads on next start"), ("b_pack (Yours)", "Off")])
        self.assertEqual(self.item(BUILTIN).checkState(0), qt.Qt.Checked)
        self.assertEqual(self.item(BUILTIN + 1).checkState(0), qt.Qt.Unchecked)
        self.assertEqual(self.section.box.text, "Tools · 7 on")
        self.assertTrue(self.section.restartBar.isHidden())  # no chat runs

    def test_a_refresh_writes_slicerflows_packs_into_the_folder(self):
        self.section.refresh()
        for name in BUILTIN_NAMES:
            self.assertTrue(os.path.isfile(builtin_path(name)), name)
        self.assertEqual(set(self.state().written), set(BUILTIN_NAMES))
        self.assertEqual(self.rows(), BUILTIN_ROWS)  # SlicerFlow's files are not rows of their own
        self.assertIn("slicerflow_", read(os.path.join(paths.user_tools_dir(), "README.txt")))

    def test_a_deleted_builtin_file_comes_back_on_refresh(self):
        self.section.refresh()
        os.remove(builtin_path("markups"))
        self.section.refresh()
        self.assertTrue(os.path.isfile(builtin_path("markups")))
        self.assertEqual(self.rows()[4], BUILTIN_ROWS[4])

    def test_a_builtin_pack_is_listed_whole_when_the_folder_refuses_its_file(self):
        """The chat then loads it from the extension, so the list shows it from there."""
        self.section.refresh()
        os.remove(builtin_path("markups"))
        os.mkdir(builtin_path("markups"))  # a directory where the file should be
        self.section.refresh()
        self.assertEqual(self.rows()[4], BUILTIN_ROWS[4])
        self.section.packTree.setCurrentItem(self.item(4))
        self.assertEqual([name for name, _what in self.tool_rows()], ["add_markup_point"])

    def test_an_updated_slicerflow_pack_replaces_the_unedited_file(self):
        self.section.refresh()
        older = "# an older SlicerFlow version\nTITLE = 'Markups'\nTOOLS = []\n"
        with open(builtin_path("markups"), "w", encoding="utf-8") as fh:
            fh.write(older)
        state = self.state()
        state.written["markups"] = user_tools.digest(older)
        user_tools.write_state(paths.user_tools_file(), state)
        self.section.refresh()
        self.assertIn("add_markup_point", read(builtin_path("markups")))
        self.assertEqual(self.rows()[4], BUILTIN_ROWS[4])

    def test_the_section_builds_when_tools_json_cannot_be_written(self):
        os.remove(paths.user_tools_file())
        os.mkdir(paths.user_tools_file())  # a directory where the file should be
        widget = qt.QWidget()
        section = tools_panel.ToolsSection(qt.QVBoxLayout(widget), restart=lambda: None)
        self.assertEqual([(section.packTree.topLevelItem(row).text(0),
                           section.packTree.topLevelItem(row).text(1))
                          for row in range(section.packTree.topLevelItemCount)], BUILTIN_ROWS)

    def test_with_no_packs_of_your_own_only_slicerflows_are_listed(self):
        self.section.refresh()
        self.assertEqual(self.rows(), BUILTIN_ROWS)
        self.assertEqual(self.section.box.text, "Tools · 6 on")

    def test_a_file_named_like_a_builtin_module_is_its_own_pack(self):
        write_pack("scene")
        self.section.refresh()
        self.assertEqual(self.rows()[0], BUILTIN_ROWS[0])
        self.assertEqual(self.user_rows(), [("scene (Yours)", "Off")])
        self.assertEqual(read(builtin_path("scene")), read(os.path.join(user_tools.BUILTIN_DIR, "scene.py")))

    def test_a_pack_of_yours_with_a_title_is_shown_by_it(self):
        write_pack("mine", 'TITLE = "My tools"\nTOOLS = []\n')
        self.section.refresh()
        self.assertEqual(self.user_rows(), [("My tools (Yours)", "Off")])

    def test_switching_a_builtin_pack_off_asks_nothing_and_saves(self):
        self.section.refresh()
        self.item(5).setCheckState(0, qt.Qt.Unchecked)
        wait_until(lambda: self.state().builtin_off == {"capture"}, what="Screenshots saved as off")
        self.assertEqual(self.asked, [])
        wait_until(lambda: self.rows()[5] == ("Screenshots (SlicerFlow)", "Off"), what="the refresh")
        self.item(5).setCheckState(0, qt.Qt.Checked)
        wait_until(lambda: self.state().builtin_off == set(), what="Screenshots saved as on")
        self.assertEqual(self.asked, [])

    def test_switching_a_builtin_pack_keeps_your_packs_on(self):
        write_pack("mine")
        write_state(enabled={"mine"})
        self.section.refresh()
        self.item(1).setCheckState(0, qt.Qt.Unchecked)
        wait_until(lambda: (self.state().enabled, self.state().builtin_off) == ({"mine"}, {"view"}),
                   what="both lists saved")

    def test_with_every_pack_off_it_says_the_assistant_can_only_answer_in_text(self):
        write_state(builtin_off=BUILTIN_NAMES)
        self.section.refresh()
        self.assertIn("No tools are on", self.section.statusLabel.text)
        self.assertEqual(self.section.box.text, "Tools")

    def test_the_status_of_a_loaded_pack_comes_from_the_running_chat(self):
        write_pack("a_pack")
        write_state(enabled={"a_pack"})
        self.fake_runtime(*self.builtin_packs(),
                          user_tools.Pack("a_pack", "a_pack.py", error="RuntimeError: boom"))
        self.section.refresh()
        self.assertEqual(self.user_rows(), [("a_pack (Yours)", "Error: RuntimeError: boom")])
        self.assertEqual(self.item(BUILTIN).toolTip(1), "Error: RuntimeError: boom")

    def test_the_restart_bar_shows_when_your_packs_change(self):
        write_pack("a_pack")
        write_pack("b_pack")
        write_state(enabled={"a_pack"})
        self.fake_runtime(*self.builtin_packs(), user_tools.Pack("a_pack", "a_pack.py"))
        self.section.refresh()
        self.assertTrue(self.section.restartBar.isHidden())
        write_state(enabled={"a_pack", "b_pack"})
        self.section.refresh()
        self.assertFalse(self.section.restartBar.isHidden())
        self.section.onRestart()
        self.assertEqual(self.restarts, [1])

    def test_the_restart_bar_shows_when_a_builtin_pack_changes(self):
        self.fake_runtime(*self.builtin_packs())
        self.section.refresh()
        self.assertTrue(self.section.restartBar.isHidden())
        write_state(builtin_off={"view"})
        self.section.refresh()
        self.assertFalse(self.section.restartBar.isHidden())

    def test_editing_a_builtin_pack_during_a_chat_calls_for_a_restart(self):
        self.fake_runtime(*self.builtin_packs())
        self.section.refresh()
        self.assertTrue(self.section.restartBar.isHidden())
        self.edit_builtin("view", "Changing the layout", "Switching the layout")
        self.section.refresh()
        self.assertFalse(self.section.restartBar.isHidden())  # the chat still runs the original

    # ── Trust ──

    def test_turning_your_pack_on_asks_first_and_saves(self):
        write_pack("mine")
        self.section.refresh()
        self.item(BUILTIN).setCheckState(0, qt.Qt.Checked)
        wait_until(lambda: self.state().enabled == {"mine"}, what="the pack to be saved as on")
        self.assertEqual(len(self.asked), 1)
        self.assertIn("full access", self.asked[0])
        self.assertIn("mine", self.asked[0])

    def test_a_refresh_while_the_trust_prompt_is_up_leaves_the_item_alone(self):
        """The prompt is modal, so the chat panel's status changes (which refresh this list)
        run while it is up; clearing the list then would delete the item whose itemChanged
        is still on the stack."""
        write_pack("mine")
        self.section.refresh()
        item = self.item(BUILTIN)
        seen = []

        def confirm(text):
            self.section.refresh()  # what a status change does while the prompt is up
            seen.append(item.text(1))  # raises if refresh() deleted the item
            return True

        self.section._confirm = confirm
        item.setCheckState(0, qt.Qt.Checked)
        wait_until(lambda: self.state().enabled == {"mine"}, what="the pack to be saved as on")
        self.assertEqual(seen, ["Off"])
        wait_until(lambda: self.user_rows() == [("mine (Yours)", "Loads on next start")],
                   what="the deferred refresh")

    def test_declining_keeps_your_pack_off(self):
        write_pack("mine")
        self.section.refresh()
        self.answer = False
        self.item(BUILTIN).setCheckState(0, qt.Qt.Checked)
        wait_until(lambda: self.section.packTree.topLevelItemCount == BUILTIN + 1
                   and self.item(BUILTIN).checkState(0) == qt.Qt.Unchecked,
                   what="the box to be cleared again")
        self.assertEqual(self.state().enabled, set())

    def test_turning_your_pack_off_does_not_ask(self):
        write_pack("mine")
        write_state(enabled={"mine"})
        self.section.refresh()
        self.item(BUILTIN).setCheckState(0, qt.Qt.Unchecked)
        wait_until(lambda: self.state().enabled == set(), what="the pack to be saved as off")
        self.assertEqual(self.asked, [])

    # ── Edited built-in packs ──

    def test_an_edited_builtin_pack_is_yours_off_and_left_alone(self):
        path = self.edit_builtin("view", "Changing the layout", "Switching the layout")
        self.section.refresh()
        self.assertEqual(self.rows()[1], ("Views (Edited)", "Off"))
        self.assertEqual(self.item(1).checkState(0), qt.Qt.Unchecked)
        self.assertIn("Switching the layout", read(path))  # never overwritten
        self.assertEqual(self.section.box.text, "Tools · 5 on")
        self.section.packTree.setCurrentItem(self.item(1))
        self.assertFalse(self.section.detailLabel.isHidden())
        self.assertIn("Edited", self.section.detailLabel.text)
        self.assertEqual(self.item(1).toolTip(0), path)
        self.assertNotIn(tools_panel.NEWER_TEXT, self.section.detailLabel.text)
        self.assertFalse(self.section.restoreButton.isHidden())
        # Each tool keeps the approval SlicerFlow's version declares.
        self.assertEqual(self.tool_rows()[0], ("set_layout", "Change the view layout of 3D Slicer."))

    def test_a_tool_added_to_an_edited_builtin_pack_is_listed_as_asking_first(self):
        """The edited file is the user's code: a tool added without permission= asks."""
        self.edit_builtin("markups", "TOOLS = [add_markup_point]",
                          ADDED_TOOL + "TOOLS = [add_markup_point, count_point_lists]")
        self.section.refresh()
        self.section.packTree.setCurrentItem(self.item(4))
        self.assertEqual([name for name, _what in self.tool_rows()],
                         ["add_markup_point", "count_point_lists (asks first)"])

    def test_turning_an_edited_builtin_pack_on_asks_first_and_saves_its_file_name(self):
        self.edit_builtin("view", "Changing the layout", "Switching the layout")
        self.section.refresh()
        self.item(1).setCheckState(0, qt.Qt.Checked)
        wait_until(lambda: self.state().enabled == {"slicerflow_view"}, what="the edited pack saved as on")
        self.assertEqual(len(self.asked), 1)
        self.assertIn("not the version SlicerFlow wrote", self.asked[0])
        self.assertIn("full access", self.asked[0])
        self.assertEqual(self.state().builtin_off, set())
        wait_until(lambda: self.rows()[1] == ("Views (Edited)", "Loads on next start"), what="the refresh")

    def test_restore_original_puts_slicerflows_version_back_and_keeps_yours(self):
        path = self.edit_builtin("view", "Changing the layout", "Switching the layout")
        write_state(enabled={"slicerflow_view"})
        self.section.refresh()
        self.section.packTree.setCurrentItem(self.item(1))
        self.section.onRestore()
        self.assertEqual(len(self.asked), 1)
        self.assertIn("slicerflow_view.py.bak", self.asked[0])
        self.assertIn("replacing any earlier", self.asked[0])  # a second restore overwrites it
        self.assertEqual(self.rows()[1], BUILTIN_ROWS[1])
        self.assertIn("Changing the layout", read(path))
        self.assertIn("Switching the layout", read(path + ".bak"))
        self.assertEqual(self.state().enabled, set())
        self.assertIn("Restart the chat", self.section.statusLabel.text)
        self.assertTrue(self.section.restoreButton.isHidden())

    def test_declining_restore_changes_nothing(self):
        path = self.edit_builtin("view", "Changing the layout", "Switching the layout")
        self.section.refresh()
        self.section.packTree.setCurrentItem(self.item(1))
        self.answer = False
        self.section.onRestore()
        self.assertIn("Switching the layout", read(path))
        self.assertEqual(self.rows()[1], ("Views (Edited)", "Off"))

    def test_a_newer_slicerflow_version_of_an_edited_pack_is_announced(self):
        self.edit_builtin("view", "Changing the layout", "Switching the layout")
        state = self.state()
        state.written["view"] = "0" * 64  # as if SlicerFlow had written an older version
        user_tools.write_state(paths.user_tools_file(), state)
        self.section.refresh()
        self.section.packTree.setCurrentItem(self.item(1))
        self.assertIn(tools_panel.NEWER_TEXT, self.section.detailLabel.text)

    # ── The selected pack ──

    def test_selecting_a_pack_shows_its_tools_file_and_kind(self):
        write_pack("mine", MINE)
        self.section.refresh()
        self.assertTrue(self.section.detailLabel.isHidden())
        self.assertTrue(self.section.toolTree.isHidden())
        self.assertTrue(self.section.editButton.isHidden())
        self.section.packTree.setCurrentItem(self.item(0))
        rows = self.tool_rows()
        self.assertEqual([name for name, _what in rows], [
            "get_scene_context", "list_sample_data", "load_sample_data (asks first)", "load_file (asks first)"])
        self.assertTrue(rows[0][1].startswith("Describe the 3D Slicer scene"))
        self.assertIn("stops responding", self.section.toolTree.topLevelItem(2).toolTip(0))
        # The row already says what it is and how many tools; its tooltip, which file.
        self.assertTrue(self.section.detailLabel.isHidden())
        self.assertFalse(self.section.editButton.isHidden())
        self.assertTrue(self.section.restoreButton.isHidden())
        self.section.packTree.setCurrentItem(self.item(BUILTIN))
        self.assertEqual(self.tool_rows(), [("say_hi (asks first)", "Greet someone."),
                                            ("count_things", "Count the things.")])
        self.assertTrue(self.section.detailLabel.isHidden())

    def test_the_selection_survives_a_refresh(self):
        self.section.refresh()
        self.section.packTree.setCurrentItem(self.item(1))
        self.section.refresh()
        current = self.section.packTree.currentItem()
        self.assertEqual(self.section.packTree.indexOfTopLevelItem(current), 1)
        self.assertEqual([name for name, _what in self.tool_rows()],
                         ["set_layout", "navigate_slice", "center_views"])

    def test_open_in_editor_and_show_in_folder_take_the_selected_file(self):
        self.section.refresh()
        self.section.packTree.setCurrentItem(self.item(2))
        self.section.onEdit()
        self.section.onShow()
        self.assertEqual(self.edited, [builtin_path("volume")])
        self.assertEqual(self.shown, [builtin_path("volume")])

    # ── Buttons ──

    def test_new_writes_the_template_turns_it_on_and_opens_it_for_editing(self):
        self.section._askName = lambda: "lung"
        self.section.onNew()
        path = user_tools.pack_path(paths.user_tools_dir(), "lung")
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(self.state().enabled, {"lung"})
        self.assertEqual(self.user_rows(), [("lung (Yours)", "Loads on next start")])
        self.assertEqual(self.edited, [path])  # a text editor, never the file's "open" action
        self.assertEqual(self.opened, [])

    def test_new_with_a_bad_name_says_why(self):
        self.section._askName = lambda: "Lung Tools"
        self.section.onNew()
        self.assertIn("lowercase", self.section.statusLabel.text)
        self.assertEqual(self.user_rows(), [])

    def test_import_asks_first_copies_and_turns_on(self):
        source = os.path.join(tempfile.mkdtemp(), "shared.py")
        with open(source, "w", encoding="utf-8") as fh:
            fh.write("TOOLS = []\n")
        self.section._pickFile = lambda: source
        self.section.onImport()
        self.assertEqual(len(self.asked), 1)
        self.assertEqual(self.state().enabled, {"shared"})
        self.assertTrue(os.path.isfile(user_tools.pack_path(paths.user_tools_dir(), "shared")))

    def test_importing_an_edited_slicerflow_pack_over_slicerflows_makes_it_edited_and_on(self):
        """Someone's edited slicerflow_scene.py, imported in place of SlicerFlow's: the import
        asks for trust, then whether to replace, and the pack is Edited, on, and loads from
        its file."""
        original = read(os.path.join(user_tools.BUILTIN_DIR, "scene.py"))
        self.assertIn("Describe the 3D Slicer scene", original)
        source = os.path.join(tempfile.mkdtemp(), "slicerflow_scene.py")
        with open(source, "w", encoding="utf-8") as fh:
            fh.write(original.replace("Describe the 3D Slicer scene", "Describe the scene"))
        self.section._pickFile = lambda: source
        self.section.onImport()
        self.assertEqual(len(self.asked), 2)
        self.assertIn("full access", self.asked[0])
        self.assertIn("Replace it?", self.asked[1])
        self.assertEqual(self.state().enabled, {"slicerflow_scene"})
        self.assertEqual(self.rows()[0], ("Scene & data (Edited)", "Loads on next start"))
        self.assertEqual(self.user_rows(), [])  # not listed twice
        from SlicerFlowLib.runtime import Runtime

        runtime = Runtime("pk_test_tools_panel", UNREACHABLE_ENGINE, lambda url: None)
        lifecycle.start(runtime)
        self.addCleanup(lifecycle.stop)
        scene = [pack for pack in runtime.packs if pack.name == "slicerflow_scene"][0]
        self.assertFalse(scene.builtin)
        self.assertEqual(scene.path, builtin_path("scene"))
        self.assertTrue(scene.tools[0].description.startswith("Describe the scene"))
        self.section.refresh()
        self.assertEqual(self.rows()[0], ("Scene & data (Edited)", "On · 4 tools"))

    def test_install_shows_only_for_a_selected_pack_that_needs_a_package(self):
        write_pack("plain")
        write_pack("needs", 'REQUIRES = {"not_a_real_package_sf": "not-a-real-package"}\n')
        self.section.refresh()
        self.assertEqual(self.user_rows()[0], ("needs (Yours)", "Needs not-a-real-package"))
        self.assertTrue(self.section.installButton.isHidden())
        self.section.packTree.setCurrentItem(self.item(BUILTIN))
        self.assertFalse(self.section.installButton.isHidden())
        self.assertEqual(self.section.installButton.text, "Install not-a-real-package")
        self.section.packTree.setCurrentItem(self.item(0))  # a built-in pack: nothing to install
        self.assertTrue(self.section.installButton.isHidden())

    def test_turning_a_pack_back_on_clears_the_text_only_line(self):
        write_state(builtin_off=BUILTIN_NAMES)
        self.section.refresh()
        self.assertIn("No tools are on", self.section.statusLabel.text)
        write_state(builtin_off=[name for name in BUILTIN_NAMES if name != "view"])
        self.section.refresh()
        self.assertNotIn("No tools are on", self.section.statusLabel.text)

    def test_a_message_from_an_action_survives_a_refresh(self):
        self.section._askName = lambda: "Lung Tools"
        self.section.onNew()
        self.section.refresh()
        self.assertIn("lowercase", self.section.statusLabel.text)
