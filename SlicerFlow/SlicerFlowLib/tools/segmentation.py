"""Tools for segmentations: how segments look, making one by threshold, measuring them."""

import json
from typing import List, Optional

import numpy
import qt
import slicer

from talk2view import tool

from SlicerFlowLib.tools import _nodes

VOLUME_CM3 = "LabelmapSegmentStatisticsPlugin.volume_cm3"
VOXELS = "LabelmapSegmentStatisticsPlugin.voxel_count"


@tool(permission=False)
def set_segment_display(
    segment_names: Optional[List[str]] = None,
    visible: Optional[bool] = None,
    opacity: Optional[float] = None,
    color: str = "",
    segmentation_name: str = "",
) -> str:
    """Show or hide segments, or change their opacity or color, in the slice and 3D views.

    Args:
        segment_names: The segments to change. Empty: every segment.
        visible: True to show them, False to hide them. Leave out to keep it as it is.
        opacity: From 0 (clear) to 1 (solid). Leave out to keep it as it is.
        color: A color name (red, orange, gold...) or a hex code such as #ff8800. Empty: keep it.
        segmentation_name: The segmentation. Empty: the only one there is.
    """
    if visible is None and opacity is None and not color:
        raise ValueError("Say what to change: visible, opacity or color.")
    if opacity is not None and not 0.0 <= opacity <= 1.0:
        raise ValueError("The opacity must be from 0 to 1.")
    rgb = None
    if color:
        qcolor = qt.QColor(color.strip())
        if not qcolor.isValid():
            raise ValueError("Unknown color {!r}. Use a name such as orange or a hex code such as #ff8800.".format(color))
        rgb = (qcolor.redF(), qcolor.greenF(), qcolor.blueF())
    node = _nodes.segmentation(segmentation_name)
    targets = _nodes.segments(node, segment_names)
    if node.GetDisplayNode() is None:
        node.CreateDefaultDisplayNodes()
    display = node.GetDisplayNode()
    if visible:
        display.SetVisibility(True)  # a hidden segmentation hides every segment
    for _name, segment_id in targets:
        if visible is not None:
            display.SetSegmentVisibility(segment_id, bool(visible))
        if opacity is not None:
            display.SetSegmentOpacity(segment_id, float(opacity))
        if rgb is not None:
            node.GetSegmentation().GetSegment(segment_id).SetColor(*rgb)
    changed = []
    if visible is not None:
        changed.append("shown" if visible else "hidden")
    if opacity is not None:
        changed.append("opacity {}".format(opacity))
    if rgb is not None:
        changed.append("color {}".format(qt.QColor(color.strip()).name()))
    return "{} ({}): {}.".format(
        node.GetName(), ", ".join(name for name, _id in targets), ", ".join(changed))


def _target_segmentation(segmentation_name, volume):
    """``(node, made_here)`` for the named segmentation (made if missing); with no name,
    the only one there is, or a new one for ``volume`` when there is none.

    ``made_here`` says whether this call put it in the scene, so a caller that fails later
    knows which node is its own to take away again."""
    if segmentation_name or not slicer.util.getNodesByClass("vtkMRMLSegmentationNode"):
        name = segmentation_name or volume.GetName() + " segmentation"
        node = slicer.mrmlScene.GetFirstNodeByName(name)
        if node is not None:
            if not node.IsA("vtkMRMLSegmentationNode"):
                raise ValueError("{!r} is not a segmentation.".format(name))
            return node, False
        node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode", name)
        node.CreateDefaultDisplayNodes()
        node.SetReferenceImageGeometryParameterFromVolumeNode(volume)
        return node, True
    return _nodes.segmentation(""), False


def _unused_segment_name(segmentation, wanted):
    """A segment name nothing in ``segmentation`` is using yet, based on ``wanted``."""
    candidate = wanted
    suffix = 1
    while segmentation.GetSegmentIdBySegmentName(candidate):
        suffix += 1
        candidate = "{} {}".format(wanted, suffix)
    return candidate


@tool(permission=True)
def threshold_segment(
    minimum: float,
    maximum: float,
    segment_name: str = "Threshold",
    volume_name: str = "",
    segmentation_name: str = "",
) -> str:
    """Make a segment of every voxel of a volume whose value is from minimum to maximum,
    for example bone in CT (about 200 to 3000). A segment with the same name is replaced.

    Args:
        minimum: The lowest value to include.
        maximum: The highest value to include.
        segment_name: The segment to make, or to replace.
        volume_name: The volume to threshold. Empty: the volume shown in the Red view.
        segmentation_name: Where the segment goes. Empty: the only segmentation, or a new one when there is none.
    """
    name = (segment_name or "").strip()
    if not name:
        raise ValueError("The segment needs a name.")
    if minimum > maximum:
        raise ValueError("The minimum ({}) is greater than the maximum ({}).".format(minimum, maximum))
    volume = _nodes.volume(volume_name)
    values = slicer.util.arrayFromVolume(volume)
    mask = ((values >= minimum) & (values <= maximum)).astype(numpy.uint8)
    count = int(mask.sum())
    if count == 0:
        low, high = volume.GetImageData().GetScalarRange()
        raise ValueError("No voxel of {} is from {} to {}: its values run from {} to {}.".format(
            volume.GetName(), minimum, maximum, low, high))
    node, made_here = _target_segmentation(segmentation_name, volume)
    segmentation = node.GetSegmentation()
    old = segmentation.GetSegmentIdBySegmentName(name)
    # The new segment is filled before the one it replaces is removed, and under another
    # name while both are there. A call that failed in between used to leave the user with
    # an empty segment under the old name, which get_scene_context then keeps reporting as
    # if the data were still there.
    staged = _unused_segment_name(segmentation, name) if old else name
    segment_id = segmentation.AddEmptySegment("", staged)
    try:
        slicer.util.updateSegmentBinaryLabelmapFromArray(mask, node, segment_id, volume)
    except BaseException:
        if made_here:
            slicer.mrmlScene.RemoveNode(node)  # nothing of the user's was in it
        else:
            segmentation.RemoveSegment(segment_id)
        raise
    if old:
        segmentation.RemoveSegment(old)
        segmentation.GetSegment(segment_id).SetName(name)
    return "{} segment {!r} in {}: {} voxels of {} from {} to {}.".format(
        "Replaced" if old else "Made", name, node.GetName(), count, volume.GetName(), minimum, maximum)


@tool(permission=False)
def measure_segment_volumes(segment_names: Optional[List[str]] = None, segmentation_name: str = "") -> str:
    """Measure the volume of segments from their voxels, in cubic centimetres (cm3; 1 cm3
    is 1 mL).

    Args:
        segment_names: The segments to measure. Empty: every segment.
        segmentation_name: The segmentation. Empty: the only one there is.
    """
    node = _nodes.segmentation(segmentation_name)
    targets = _nodes.segments(node, segment_names)
    if not targets:
        raise ValueError("{} has no segments.".format(node.GetName()))
    import SegmentStatistics

    # SegmentStatisticsLogic keeps its settings in a scene node, which is hidden but saved
    # with the user's scene, and it takes the Segment Statistics module's own node when the
    # user has that module open. So take away only a node this call added: measuring is a
    # question, and it must leave the scene as it found it. The count is taken before the
    # logic is built because its constructor is already where the node appears.
    before = {n.GetID() for n in slicer.util.getNodesByClass("vtkMRMLScriptedModuleNode")}
    logic = SegmentStatistics.SegmentStatisticsLogic()
    parameters = logic.getParameterNode()
    try:
        parameters.SetParameter("Segmentation", node.GetID())
        parameters.SetParameter("visibleSegmentsOnly", "False")
        for plugin, enabled in (
            ("LabelmapSegmentStatisticsPlugin", "True"),
            ("ScalarVolumeSegmentStatisticsPlugin", "False"),
            ("ClosedSurfaceSegmentStatisticsPlugin", "False"),
        ):
            parameters.SetParameter(plugin + ".enabled", enabled)
        logic.computeStatistics()
        stats = logic.getStatistics()
    finally:
        if parameters.GetID() not in before:
            slicer.mrmlScene.RemoveNode(parameters)
    rows = [
        {
            "segment": name,
            "volume_cm3": round(float(stats.get((segment_id, VOLUME_CM3), 0.0)), 3),
            "voxels": int(stats.get((segment_id, VOXELS), 0)),
        }
        for name, segment_id in targets
    ]
    return json.dumps({"segmentation": node.GetName(), "segments": rows})


TITLE = "Segmentation"
TOOLS = [set_segment_display, threshold_segment, measure_segment_volumes]
#: ``talk2view.ui.ToolPresentation`` arguments per tool (see ``scene.PRESENTATION``).
PRESENTATION = {
    "set_segment_display": {"activity": "Changing how segments look"},
    "threshold_segment": {
        "activity": "Making a segment by threshold",
        "destructive": True,
        "warning": "A segment with the same name is replaced.",
    },
    "measure_segment_volumes": {"activity": "Measuring segment volumes"},
}
