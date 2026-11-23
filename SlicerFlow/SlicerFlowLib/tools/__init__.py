"""The Slicer tools the Talk2View agent can call.

Each tool is a plain function: its signature and Google-style docstring become the tool's
schema (the docstring's first paragraph is its description). Handlers run on Slicer's main
thread (the host's dispatcher), so they use the Slicer API directly. A handler never calls
``slicer.app.processEvents()``: that re-enters the main-thread queue it was posted through.

``permission=True`` tools wait for the user's approval in the chat. No tool runs arbitrary
code, clears the scene or saves files (``NEVER``). Every tool says ``permission=True`` or
``permission=False`` itself: SlicerFlow writes these packs into the user's tools folder, and
an edited copy there loads like a user's pack, where a tool that does not say asks first.

No ``from __future__ import annotations`` in the tool modules: the schema is built from
real type annotations.

Each module is a built-in pack: its own ``TITLE``, ``TOOLS`` and ``PRESENTATION``, the names a
user's pack uses (``user_tools``). ``BUILTIN_PACKS`` is their order; ``TOOLS`` and
``PRESENTATION`` here are derived from them.
"""

from . import capture, markups, scene, segmentation, view, volume

#: The built-in packs, in the order their tools reach the engine.
BUILTIN_PACKS = ("scene", "view", "volume", "segmentation", "markups", "capture")
MODULES = {
    "scene": scene,
    "view": view,
    "volume": volume,
    "segmentation": segmentation,
    "markups": markups,
    "capture": capture,
}

TOOLS = [tool for name in BUILTIN_PACKS for tool in MODULES[name].TOOLS]
#: ``talk2view.ui.ToolPresentation`` arguments per tool, from every built-in pack.
PRESENTATION = {
    tool: spec for name in BUILTIN_PACKS for tool, spec in MODULES[name].PRESENTATION.items()
}

#: Names that must never become tools.
NEVER = ("execute_python", "clear_scene", "save_scene", "save_file", "save_node")
