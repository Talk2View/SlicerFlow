"""The system prompt follows the tools that are on."""

from SlicerFlowLib.prompt import system_prompt

#: The prompt SlicerFlow sent before the built-in tools became packs, word for word.
BEFORE = (
    "You are Talk2View, an assistant inside 3D Slicer, the medical imaging application. "
    "Use your tools to look at and change the user's scene. Call get_scene_context before "
    "changing, measuring or segmenting loaded volumes, segmentations or models; layout and "
    "view changes don't need it. Say briefly what you changed. You are not a medical device: "
    "never give a diagnosis."
)
ALL = ["get_scene_context", "list_sample_data", "load_sample_data", "load_file", "set_layout",
       "navigate_slice", "center_views", "set_window_level", "set_volume_rendering",
       "set_segment_display", "threshold_segment", "measure_segment_volumes",
       "add_markup_point", "capture_view"]


def test_with_every_tool_on_the_prompt_is_the_one_from_before():
    assert system_prompt(ALL) == BEFORE


def test_without_scene_and_data_it_does_not_mention_get_scene_context():
    prompt = system_prompt([name for name in ALL[4:]])
    assert "get_scene_context" not in prompt
    assert "Use your tools" in prompt and "never give a diagnosis" in prompt


def test_with_no_tools_it_says_so():
    prompt = system_prompt([])
    assert "no Slicer tools" in prompt and "Use your tools" not in prompt
    assert "never give a diagnosis" in prompt
