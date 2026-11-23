"""The user's tool packs in a running chat: a pack that is on registers its tools with the
SDK and the chat page; one that is off, broken or missing a package adds nothing, and the
chat starts regardless."""

import os
import shutil
import tempfile
import textwrap
import unittest

import sf_testing  # noqa: F401  (isolates SLICERFLOW_HOME first)

from sf_testing import UNREACHABLE_ENGINE
from SlicerFlowLib import lifecycle, paths, user_tools
from SlicerFlowLib.tools import TOOLS

PACK = '''
    import slicer
    from talk2view import tool


    @tool
    def add_named_point(name: str) -> str:
        """Add a markup point list with one point at the origin.

        Args:
            name: The point list's name.
        """
        node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode", name)
        node.AddControlPoint(0, 0, 0)
        return "Added " + name


    @tool(permission=False)
    def count_point_lists() -> str:
        """Count the point lists in the scene."""
        return str(len(slicer.util.getNodesByClass("vtkMRMLMarkupsFiducialNode")))


    TOOLS = [add_named_point, count_point_lists]
    PRESENTATION = {"add_named_point": {"activity": "Adding a point"}}
'''


def write_pack(name, source, enabled=True):
    folder = user_tools.ensure_user_dir(paths.user_tools_dir())
    with open(user_tools.pack_path(folder, name), "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent(source))
    if enabled:
        state = user_tools.read_state(paths.user_tools_file())
        state.enabled.add(name)
        user_tools.write_state(paths.user_tools_file(), state)


#: A tool added to an edited copy of SlicerFlow's markups pack, saying nothing about asking.
ADDED_TOOL = '''@tool
def count_point_lists() -> str:
    """Count the point lists in the scene."""
    return str(len(slicer.util.getNodesByClass("vtkMRMLMarkupsFiducialNode")))


'''


def tools_module(name):
    """SlicerFlow's own module for the built-in pack ``name``."""
    from SlicerFlowLib import tools

    return tools.MODULES[name]


def user_packs(runtime):
    """The user's packs a runtime loaded (``Runtime.packs`` holds the built-in ones first)."""
    return [pack for pack in runtime.packs if not pack.builtin]


class UserToolsTest(unittest.TestCase):
    def setUp(self):
        # A home of its own: the packs and tools.json of one test never reach the next.
        saved = os.environ.get("SLICERFLOW_HOME")
        os.environ["SLICERFLOW_HOME"] = tempfile.mkdtemp(prefix="sf-user-tools-")
        self.addCleanup(
            lambda: os.environ.__setitem__("SLICERFLOW_HOME", saved) if saved is not None
            else os.environ.pop("SLICERFLOW_HOME", None)
        )
        self.addCleanup(lifecycle.stop)

    def start(self):
        from SlicerFlowLib.runtime import Runtime

        runtime = Runtime("pk_test_user_tools", UNREACHABLE_ENGINE, lambda url: None)
        lifecycle.start(runtime)
        return runtime

    def test_a_pack_that_is_on_registers_its_tools(self):
        from SlicerFlowLib.runtime import Runtime

        write_pack("points", PACK)
        runtime = self.start()
        self.assertEqual([(pack.name, pack.error) for pack in user_packs(runtime)], [("points", None)])
        names = [tool.name for tool in runtime.tools]
        self.assertEqual(names, [tool.name for tool in TOOLS] + ["add_named_point",
                                                                  "count_point_lists"])
        self.assertTrue(runtime.t2v.tools.has_handler("add_named_point"))
        self.assertTrue(runtime.t2v.tools.has_handler("count_point_lists"))
        self.assertEqual([tool._t2v_permission for tool in user_packs(runtime)[0].tools], [True, False])
        options = Runtime._base_options(dictation=False, presentation=runtime.presentation)
        self.assertEqual(options.tools["add_named_point"].activity, "Adding a point")

    def test_off_broken_and_missing_packs_add_nothing_and_the_chat_starts(self):
        write_pack("off", PACK, enabled=False)
        write_pack("broken", "raise RuntimeError('boom')\n")
        write_pack("needs", 'REQUIRES = {"not_a_real_package_sf": "not-a-real-package"}\n'
                            "import not_a_real_package_sf\n")
        runtime = self.start()
        self.assertEqual(
            {pack.name: (pack.error, pack.missing) for pack in user_packs(runtime)},
            {"broken": ("RuntimeError: boom", {}),
             "needs": (None, {"not_a_real_package_sf": "not-a-real-package"})},
        )
        self.assertEqual([tool.name for tool in runtime.tools], [tool.name for tool in TOOLS])
        self.assertFalse(runtime.t2v.tools.has_handler("add_named_point"))

    def test_a_pack_with_a_bad_permission_does_not_stop_the_chat(self):
        """The SDK's handle() raises TypeError for a permission that is neither a bool nor
        callable; that must fail this pack, not Runtime.start()."""
        write_pack("typo", PACK.replace("@tool\n", '@tool(permission="ask")\n'))
        runtime = self.start()
        self.assertIn("permission='ask'", user_packs(runtime)[0].error)
        self.assertEqual([tool.name for tool in runtime.tools], [tool.name for tool in TOOLS])

    def test_a_pack_cannot_take_a_slicerflow_tool_name(self):
        write_pack("sneaky", PACK.replace("add_named_point", "load_file"))
        runtime = self.start()
        self.assertEqual(user_packs(runtime)[0].error,
                         "load_file is already a tool of SlicerFlow: rename yours.")

    def test_the_template_loads_in_slicer(self):
        path = user_tools.new_pack(paths.user_tools_dir(), "mine")
        pack = user_tools.load_pack("mine", path, {tool.name: "SlicerFlow" for tool in TOOLS})
        self.assertIsNone(pack.error)
        self.assertEqual([(tool.name, tool._t2v_permission) for tool in pack.tools],
                         [("mine_count_volumes", False)])
        self.assertIn("volume(s) loaded", pack.tools[0]())

    def test_every_builtin_module_is_a_valid_pack_whose_tools_keep_their_permission(self):
        from SlicerFlowLib import tools

        taken = {}
        asking = set()
        for name in tools.BUILTIN_PACKS:
            pack = user_tools.builtin_pack(name, tools.MODULES[name], taken)
            self.assertIsNone(pack.error, name)
            self.assertTrue(pack.builtin)
            taken.update((tool.name, pack.title) for tool in pack.tools)
            asking.update(tool.name for tool in pack.tools if tool._t2v_permission is True)
        self.assertEqual(asking, {"load_sample_data", "load_file", "threshold_segment", "capture_view"})

    def set_builtin_off(self, *names):
        state = user_tools.read_state(paths.user_tools_file())
        state.builtin_off = set(names)
        user_tools.write_state(paths.user_tools_file(), state)

    def test_a_builtin_pack_that_is_off_registers_nothing(self):
        self.set_builtin_off("capture", "no_such_pack")
        runtime = self.start()
        names = [tool.name for tool in runtime.tools]
        self.assertNotIn("capture_view", names)
        self.assertFalse(runtime.t2v.tools.has_handler("capture_view"))
        self.assertEqual(names, [tool.name for tool in TOOLS if tool.name != "capture_view"])
        self.assertEqual([pack.name for pack in runtime.packs if pack.builtin],
                         ["scene", "view", "volume", "segmentation", "markups"])

    def test_with_scene_off_the_prompt_does_not_mention_get_scene_context(self):
        self.set_builtin_off("scene")
        runtime = self.start()
        self.assertNotIn("get_scene_context", runtime.prompt)
        self.assertIn("Use your tools", runtime.prompt)

    def test_a_user_pack_cannot_take_a_builtin_name_while_its_pack_is_off(self):
        self.set_builtin_off("scene")
        write_pack("sneaky", PACK.replace("add_named_point", "get_scene_context"))
        runtime = self.start()
        sneaky = [pack for pack in runtime.packs if pack.name == "sneaky"][0]
        self.assertEqual(sneaky.error, "get_scene_context is already a tool of SlicerFlow: rename yours.")

    def test_with_every_pack_off_the_chat_starts_without_tools(self):
        from SlicerFlowLib import tools

        self.set_builtin_off(*tools.BUILTIN_PACKS)
        runtime = self.start()
        self.assertEqual(runtime.tools, [])
        self.assertIn("no Slicer tools", runtime.prompt)

    def test_a_second_start_never_makes_a_quiet_builtin_tool_ask(self):
        self.start()
        lifecycle.stop()
        runtime = self.start()
        asking = {tool.name for tool in runtime.tools if tool._t2v_permission is True}
        self.assertEqual(asking, {"load_sample_data", "load_file", "threshold_segment", "capture_view"})

    def test_every_builtin_pack_loads_from_a_copy_outside_the_package(self):
        """SlicerFlow writes its packs into the tools folder, and an edited one is loaded
        from there by path, like a user's pack: the copy must import nothing relatively."""
        from SlicerFlowLib import tools

        folder = user_tools.ensure_user_dir(paths.user_tools_dir())
        for name in tools.BUILTIN_PACKS:
            path = user_tools.pack_path(folder, "copy_of_" + name)
            shutil.copyfile(os.path.join(user_tools.BUILTIN_DIR, name + ".py"), path)
            pack = user_tools.load_pack("copy_of_" + name, path, {})
            self.assertIsNone(pack.error, name)
            self.assertEqual([tool.name for tool in pack.tools],
                             [tool.name for tool in tools.MODULES[name].TOOLS], name)

    def test_a_start_writes_slicerflows_packs_into_the_tools_folder(self):
        from SlicerFlowLib import tools

        runtime = self.start()
        folder = paths.user_tools_dir()
        for name in tools.BUILTIN_PACKS:
            path = user_tools.pack_path(folder, "slicerflow_" + name)
            self.assertTrue(os.path.isfile(path), path)
            with open(os.path.join(user_tools.BUILTIN_DIR, name + ".py"), encoding="utf-8") as fh:
                source = fh.read()
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), source)
        state = user_tools.read_state(paths.user_tools_file())
        self.assertEqual(set(state.written), set(tools.BUILTIN_PACKS))
        self.assertEqual([pack.path for pack in runtime.packs],
                         [user_tools.pack_path(folder, "slicerflow_" + name) for name in tools.BUILTIN_PACKS])
        # The folder copies are not loaded as user packs on top: today's tools, once each.
        self.assertEqual([tool.name for tool in runtime.tools], [tool.name for tool in TOOLS])

    def edit_builtin(self, name, old, new):
        """Write SlicerFlow's packs into the folder (as a start would) and change one."""
        folder = user_tools.ensure_user_dir(paths.user_tools_dir())
        state = user_tools.read_state(paths.user_tools_file())
        user_tools.sync_builtins(folder, state, user_tools.builtin_sources(user_tools.BUILTIN_DIR))
        user_tools.write_state(paths.user_tools_file(), state)
        path = user_tools.pack_path(folder, "slicerflow_" + name)
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn(old, source)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(source.replace(old, new))
        return path

    def turn_on(self, stem):
        state = user_tools.read_state(paths.user_tools_file())
        state.enabled.add(stem)
        user_tools.write_state(paths.user_tools_file(), state)

    def test_an_edited_builtin_pack_is_off_until_turned_on_then_loads_from_its_file(self):
        self.edit_builtin("view", '"Layout set to {}."', '"Layout now {}."')
        runtime = self.start()
        names = [tool.name for tool in runtime.tools]
        self.assertNotIn("set_layout", names)  # not the version SlicerFlow wrote: off
        self.assertNotIn("view", [pack.name for pack in runtime.packs])
        self.assertIn("get_scene_context", names)  # the other packs are as before
        lifecycle.stop()
        self.turn_on("slicerflow_view")
        runtime = self.start()
        edited = [pack for pack in runtime.packs if pack.name == "slicerflow_view"][0]
        self.assertIsNone(edited.error)
        self.assertFalse(edited.builtin)
        self.assertTrue(runtime.t2v.tools.has_handler("set_layout"))
        self.assertEqual(edited.tools[0](layout="conventional"), "Layout now conventional.")
        # Each tool keeps the approval its source declares, as in SlicerFlow's own version.
        self.assertEqual([(tool.name, tool._t2v_permission) for tool in edited.tools],
                         [(tool.name, tool._t2v_permission) for tool in tools_module("view").TOOLS])
        # It loads after SlicerFlow's unedited packs and before the user's, keeping its names.
        self.assertEqual([pack.name for pack in runtime.packs],
                         ["scene", "volume", "segmentation", "markups", "capture", "slicerflow_view"])
        # An unedited pack's names stay reserved for SlicerFlow.
        write_pack("sneaky", PACK.replace("add_named_point", "get_scene_context"))
        lifecycle.stop()
        runtime = self.start()
        sneaky = [pack for pack in runtime.packs if pack.name == "sneaky"][0]
        self.assertEqual(sneaky.error, "get_scene_context is already a tool of SlicerFlow: rename yours.")

    def test_a_tool_added_to_an_edited_builtin_pack_asks_first_unless_it_says_not(self):
        """The edited file is the user's code now: a tool they add without permission= asks
        first, like any tool of theirs, while SlicerFlow's own keep what they declare."""
        self.edit_builtin("markups", "TOOLS = [add_markup_point]",
                          ADDED_TOOL + "TOOLS = [add_markup_point, count_point_lists]")
        self.turn_on("slicerflow_markups")
        runtime = self.start()
        edited = [pack for pack in runtime.packs if pack.name == "slicerflow_markups"][0]
        self.assertIsNone(edited.error)
        self.assertEqual([(tool.name, tool._t2v_permission) for tool in edited.tools],
                         [("add_markup_point", False), ("count_point_lists", True)])

    def test_a_tools_json_that_cannot_be_written_does_not_stop_the_chat(self):
        """The first start writes SlicerFlow's packs and records them in tools.json; when
        that file cannot be replaced the chat starts anyway, with every built-in tool."""
        os.makedirs(paths.user_tools_file())  # a directory where the file should be
        runtime = self.start()
        self.assertEqual([tool.name for tool in runtime.tools], [tool.name for tool in TOOLS])
        self.assertEqual(len(runtime.tools), 14)

