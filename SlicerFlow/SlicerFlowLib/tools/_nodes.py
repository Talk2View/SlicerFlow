"""What the tools share: finding the node or view a call names, and where ``capture_view``
sends its screenshots.

Imported absolutely (``from SlicerFlowLib.tools import _nodes``), never relatively: SlicerFlow
writes its packs into the user's tools folder, and an edited copy there runs by path,
outside this package.

A tool raises ``ValueError`` for anything the model can fix, such as a wrong name or a
value out of range. The SDK sends ``{"error": <message>}`` back as the tool's result and
the model can try again, so each message says what would work.
"""

import slicer

#: The model's names for the slice views, and Slicer's.
SLICE_VIEWS = {"red": "Red", "yellow": "Yellow", "green": "Green"}

#: ``capture_view``'s destination, ``sink(png_bytes, label) -> str``, or None when no chat
#: runs; the running chat sets it through ``capture.set_sink``. Kept here, not in
#: ``capture``, so an edited copy of that pack in the user's tools folder (a module of its
#: own) sends its screenshots to the same chat.
screenshot_sink = None


def choose(value, options, what):
    """``value`` matched to one of ``options`` ignoring case; returns the option's spelling."""
    wanted = (value or "").strip().lower()
    for option in options:
        if option.lower() == wanted:
            return option
    raise ValueError("Unknown {} {!r}. Choose one of: {}.".format(what, value, ", ".join(options)))


def volume(volume_name=""):
    """The scalar volume named ``volume_name``. With no name: the one in the Red view, else
    the first one."""
    if volume_name:
        node = slicer.mrmlScene.GetFirstNodeByName(volume_name)
        if node is None or not node.IsA("vtkMRMLScalarVolumeNode"):
            raise ValueError("No volume named {!r} is loaded.".format(volume_name))
        return node
    layout = slicer.app.layoutManager()
    red = layout.sliceWidget("Red") if layout is not None else None
    if red is not None:
        volume_id = red.sliceLogic().GetSliceCompositeNode().GetBackgroundVolumeID()
        node = slicer.mrmlScene.GetNodeByID(volume_id) if volume_id else None
        if node is not None:
            return node
    volumes = slicer.util.getNodesByClass("vtkMRMLScalarVolumeNode")
    if not volumes:
        raise ValueError("No volume is loaded. Load one first (load_sample_data or load_file).")
    return volumes[0]


def slice_widget(view):
    """``(qMRMLSliceWidget, "red"|"yellow"|"green")`` for the model's view name."""
    key = choose(view, list(SLICE_VIEWS), "view")
    return slicer.app.layoutManager().sliceWidget(SLICE_VIEWS[key]), key


def slice_range(widget):
    """``[lowest, highest]`` slice offset in mm that still shows the view's volume, or None
    when the view shows no volume."""
    if widget.sliceLogic().GetBackgroundLayer().GetVolumeNode() is None:
        return None
    bounds = [0.0] * 6
    widget.sliceLogic().GetLowestVolumeSliceBounds(bounds)
    return [round(bounds[4], 2), round(bounds[5], 2)]


def segmentation(segmentation_name=""):
    """The segmentation named ``segmentation_name``. With no name: the only one there is."""
    if segmentation_name:
        node = slicer.mrmlScene.GetFirstNodeByName(segmentation_name)
        if node is None or not node.IsA("vtkMRMLSegmentationNode"):
            raise ValueError("No segmentation named {!r}.".format(segmentation_name))
        return node
    nodes = slicer.util.getNodesByClass("vtkMRMLSegmentationNode")
    if not nodes:
        raise ValueError("There is no segmentation. threshold_segment makes one.")
    if len(nodes) > 1:
        raise ValueError("There are several segmentations ({}). Name one.".format(
            ", ".join(node.GetName() for node in nodes)))
    return nodes[0]


def segments(segmentation_node, names=None):
    """``[(name, segment id)]`` for ``names`` (matched ignoring case; every segment when
    empty), in the order given."""
    segmentation = segmentation_node.GetSegmentation()
    ids = [segmentation.GetNthSegmentID(i) for i in range(segmentation.GetNumberOfSegments())]
    found = [(segmentation.GetSegment(i).GetName(), i) for i in ids]
    if not names:
        return found
    by_name = {name.lower(): (name, segment_id) for name, segment_id in found}
    missing = [name for name in names if name.strip().lower() not in by_name]
    if missing:
        raise ValueError("{} has no segment named {}. Its segments: {}.".format(
            segmentation_node.GetName(), ", ".join(repr(m) for m in missing),
            ", ".join(name for name, _id in found) or "none"))
    return [by_name[name.strip().lower()] for name in names]
