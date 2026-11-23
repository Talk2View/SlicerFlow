"""Tools that change what the views show and where they look."""

import slicer

from talk2view import tool

from SlicerFlowLib.tools import _nodes

#: The model's layout names and Slicer's layout constants.
LAYOUTS = {
    "conventional": "SlicerLayoutConventionalView",
    "four-up": "SlicerLayoutFourUpView",
    "3d-only": "SlicerLayoutOneUp3DView",
    "red-only": "SlicerLayoutOneUpRedSliceView",
    "yellow-only": "SlicerLayoutOneUpYellowSliceView",
    "green-only": "SlicerLayoutOneUpGreenSliceView",
    "dual-3d": "SlicerLayoutDual3DView",
}


@tool(permission=False)
def set_layout(layout: str) -> str:
    """Change the view layout of 3D Slicer.

    Args:
        layout: One of conventional, four-up, 3d-only, red-only, yellow-only, green-only or dual-3d.
    """
    name = _nodes.choose(layout, list(LAYOUTS), "layout")
    slicer.app.layoutManager().setLayout(getattr(slicer.vtkMRMLLayoutNode, LAYOUTS[name]))
    return "Layout set to {}.".format(name)


@tool(permission=False)
def navigate_slice(offset_mm: float, view: str = "red", relative: bool = False) -> str:
    """Move a slice view through the volume. get_scene_context gives each view's offset and
    the range that shows the volume.

    Args:
        offset_mm: Where to put the slice, in mm along the view's axis (red: inferior to superior, yellow: right to left, green: posterior to anterior). With relative, how far to move it.
        view: red, yellow or green.
        relative: True to move by offset_mm from where the slice is now.
    """
    widget, key = _nodes.slice_widget(view)
    node = widget.mrmlSliceNode()
    node.SetSliceOffset(node.GetSliceOffset() + offset_mm if relative else offset_mm)
    now = node.GetSliceOffset()
    span = _nodes.slice_range(widget)
    if span is None:
        # Without this the answer is just an offset, which reads as if the slice had moved
        # to something.
        note = " No volume is shown in that view (load one with load_sample_data or load_file)."
    elif not span[0] <= now <= span[1]:
        note = " That is outside the volume, which spans {} to {} mm.".format(*span)
    else:
        note = ""
    return "The {} view is at {:.1f} mm.{}".format(key, now, note)


@tool(permission=False)
def center_views() -> str:
    """Reset the slice views and the 3D view so the loaded data fills them."""
    slicer.util.resetSliceViews()
    slicer.app.layoutManager().resetThreeDViews()
    return "Views centered."


TITLE = "Views"
TOOLS = [set_layout, navigate_slice, center_views]
#: ``talk2view.ui.ToolPresentation`` arguments per tool (see ``scene.PRESENTATION``).
PRESENTATION = {
    "set_layout": {"activity": "Changing the layout"},
    "navigate_slice": {"activity": "Moving a slice"},
    "center_views": {"activity": "Centering the views"},
}
