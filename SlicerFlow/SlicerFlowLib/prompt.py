"""The system prompt: what the assistant is told about itself, which depends on the tools
that are on in this chat (``Runtime``). With every built-in pack on it is the prompt
SlicerFlow always sent. No ``slicer`` here, so the unit tests import it."""

INTRO = "You are Talk2View, an assistant inside 3D Slicer, the medical imaging application. "
WITH_TOOLS = "Use your tools to look at and change the user's scene. "
SCENE_FIRST = (
    "Call get_scene_context before changing, measuring or segmenting loaded volumes, "
    "segmentations or models; layout and view changes don't need it. "
)
SAY_WHAT = "Say briefly what you changed. "
NO_TOOLS = (
    "You have no Slicer tools in this chat, so you cannot look at or change the scene: "
    "answer from what the user tells you. "
)
NOT_A_DEVICE = "You are not a medical device: never give a diagnosis."


def system_prompt(tool_names):
    names = set(tool_names)
    if not names:
        return INTRO + NO_TOOLS + NOT_A_DEVICE
    scene = SCENE_FIRST if "get_scene_context" in names else ""
    return INTRO + WITH_TOOLS + scene + SAY_WHAT + NOT_A_DEVICE
