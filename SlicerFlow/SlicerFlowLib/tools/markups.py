"""Tools for markups."""

import slicer

from talk2view import tool

DEFAULT_LIST = "Talk2View points"


@tool(permission=False)
def add_markup_point(r: float, a: float, s: float, label: str = "", list_name: str = DEFAULT_LIST) -> str:
    """Put a point markup at a position in the scene, in RAS coordinates (mm), for example
    to mark something the user asked about.

    Args:
        r: Position along the left (-) to right (+) axis, in mm.
        a: Position along the posterior (-) to anterior (+) axis, in mm.
        s: Position along the inferior (-) to superior (+) axis, in mm.
        label: The point's label. Empty: numbered automatically.
        list_name: The point list to add it to; made when it does not exist.
    """
    node = slicer.mrmlScene.GetFirstNodeByName(list_name)
    if node is not None and not node.IsA("vtkMRMLMarkupsFiducialNode"):
        raise ValueError("{!r} is not a point list. Name another list_name.".format(list_name))
    if node is None:
        node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode", list_name)
        node.CreateDefaultDisplayNodes()
    index = node.AddControlPoint([r, a, s], (label or "").strip())
    return "Added point {} to {} at R {:.1f}, A {:.1f}, S {:.1f}.".format(
        node.GetNthControlPointLabel(index), node.GetName(), r, a, s)


TITLE = "Markups"
TOOLS = [add_markup_point]
#: ``talk2view.ui.ToolPresentation`` arguments per tool (see ``scene.PRESENTATION``).
PRESENTATION = {
    "add_markup_point": {"activity": "Adding a point"},
}
