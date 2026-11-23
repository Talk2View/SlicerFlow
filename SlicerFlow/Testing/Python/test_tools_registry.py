"""The tool set as the engine sees it: names, approvals, presentation and schemas."""

import json
import unittest

import sf_testing  # noqa: F401  (isolates SLICERFLOW_HOME first)

from sf_testing import UNREACHABLE_ENGINE
from talk2view import T2VError, Talk2View
from talk2view.ui import ChatOptions, ToolPresentation
from SlicerFlowLib.tools import NEVER, PRESENTATION, TOOLS

EXPECTED = {
    "get_scene_context", "list_sample_data", "load_sample_data", "load_file", "set_layout",
    "navigate_slice", "center_views", "set_window_level", "set_volume_rendering",
    "set_segment_display", "threshold_segment", "measure_segment_volumes", "add_markup_point",
    "capture_view",
}
NEEDS_APPROVAL = {"load_sample_data", "load_file", "threshold_segment", "capture_view"}
#: The tools whose approval card carries a warning of its own. Named here rather than read
#: back from ``PRESENTATION``, so that dropping the mark from a tool — the tidy-up that
#: quietly takes the warning off its card — fails. Which tools may be "always allowed" is a
#: separate rule now: none of them may be, because they all ask.
DESTRUCTIVE = {"load_sample_data", "load_file", "threshold_segment", "capture_view"}


def _names():
    return {tool.name for tool in TOOLS}


class ToolRegistryTest(unittest.TestCase):
    def test_the_tool_names(self):
        self.assertEqual(_names(), EXPECTED)
        self.assertEqual(len(TOOLS), len(EXPECTED))

    def test_never_a_forbidden_tool(self):
        self.assertFalse(_names() & set(NEVER))

    def test_which_tools_wait_for_approval(self):
        asking = {tool.name for tool in TOOLS if tool._t2v_permission is True}
        self.assertEqual(asking, NEEDS_APPROVAL)

    def test_every_tool_has_a_presentation_the_ui_accepts(self):
        self.assertEqual(set(PRESENTATION), _names())
        ChatOptions(tools={name: ToolPresentation(**spec) for name, spec in PRESENTATION.items()})

    def test_which_tools_are_marked_destructive_and_why(self):
        marked = {name for name, spec in PRESENTATION.items() if spec.get("destructive")}
        self.assertEqual(marked, DESTRUCTIVE)
        for name in DESTRUCTIVE:
            self.assertTrue(PRESENTATION[name].get("warning"), "{} has no warning".format(name))

    def test_the_chat_page_is_told_which_tools_are_destructive(self):
        # What ``Runtime._base_options()`` hands the page, not just the dict behind it: the
        # card's warning and its missing "Always allow" button both come from here.
        from SlicerFlowLib.runtime import Runtime

        tools = Runtime._base_options(dictation=False).tools
        self.assertEqual({name for name in tools if tools[name].destructive}, DESTRUCTIVE)
        for name in DESTRUCTIVE:
            self.assertTrue(tools[name].warning, "{} has no warning".format(name))

    def test_every_tool_that_asks_carries_a_warning_of_its_own(self):
        """Each of them asks every time, and each gets one card per call, so the card is
        the only place the user is told what this one does."""
        self.assertEqual(DESTRUCTIVE, NEEDS_APPROVAL)

    def test_every_schema_describes_the_tool_and_each_argument(self):
        for tool in TOOLS:
            schema = tool._t2v_tool_schema
            self.assertTrue(schema.description.strip(), tool.name)
            for arg, prop in schema.parameters["properties"].items():
                self.assertTrue(prop.get("description"), "{}.{}".format(tool.name, arg))

    def test_a_tool_error_reaches_the_model_as_an_error_result(self):
        t2v = Talk2View("pk_test_registry", base_url=UNREACHABLE_ENGINE)
        try:
            try:
                t2v.tools.register_from_functions(TOOLS)
            except T2VError:
                pass  # not signed in: the handlers are registered all the same
            result, is_error = t2v.tools.execute_tool_call("set_layout", {"layout": "sideways"})
            self.assertTrue(is_error)
            self.assertIn("Unknown layout", json.loads(result)["error"])
            result, is_error = t2v.tools.execute_tool_call("set_layout", {"layout": "FOUR-UP"})
            self.assertFalse(is_error)
            self.assertEqual(result, "Layout set to four-up.")
        finally:
            t2v.close()
