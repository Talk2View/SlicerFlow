"""Tools that read the scene or add data to it."""

import json
import os

import slicer

from talk2view import tool

from SlicerFlowLib.tools import _nodes
from SlicerFlowLib.tools.view import LAYOUTS

#: Sample categories that are for Slicer's own developers and tests.
HIDDEN_SAMPLE_CATEGORIES = ("Development", "Testing")
#: Files these tools never open: a scene replaces the user's work.
SCENE_SUFFIXES = (".mrb", ".mrml")


@tool(permission=False)
def get_scene_context() -> str:
    """Describe the 3D Slicer scene: the volumes (size, spacing, value range, window/level),
    the segmentations and their segments, the models, the markups, the view layout, and
    where each slice view is. Call this before changing, measuring or segmenting loaded data;
    layout and view changes don't need it."""
    volumes = []
    for node in slicer.util.getNodesByClass("vtkMRMLScalarVolumeNode"):
        image = node.GetImageData()
        display = node.GetDisplayNode()
        entry = {
            "name": node.GetName(),
            "dimensions": list(image.GetDimensions()) if image is not None else None,
            "spacing_mm": [round(s, 3) for s in node.GetSpacing()],
            "value_range": [round(v, 2) for v in image.GetScalarRange()] if image is not None else None,
        }
        if display is not None and display.IsA("vtkMRMLScalarVolumeDisplayNode"):
            entry["window"] = round(display.GetWindow(), 2)
            entry["level"] = round(display.GetLevel(), 2)
        volumes.append(entry)
    segmentations = []
    for node in slicer.util.getNodesByClass("vtkMRMLSegmentationNode"):
        segmentation = node.GetSegmentation()
        names = [segmentation.GetNthSegment(i).GetName() for i in range(segmentation.GetNumberOfSegments())]
        segmentations.append({"name": node.GetName(), "segments": names})
    models = [
        node.GetName()
        for node in slicer.util.getNodesByClass("vtkMRMLModelNode")
        if not node.GetHideFromEditors()
    ]
    markups = [
        {"name": node.GetName(), "points": node.GetNumberOfControlPoints()}
        for node in slicer.util.getNodesByClass("vtkMRMLMarkupsNode")
    ]
    layout_id = slicer.app.layoutManager().layout
    layout = next(
        (name for name, attr in LAYOUTS.items() if getattr(slicer.vtkMRMLLayoutNode, attr) == layout_id),
        "other ({})".format(layout_id),
    )
    views = {}
    for key in _nodes.SLICE_VIEWS:
        widget, _key = _nodes.slice_widget(key)
        views[key] = {
            "offset_mm": round(widget.mrmlSliceNode().GetSliceOffset(), 2),
            "range_mm": _nodes.slice_range(widget),
        }
    return json.dumps({
        "volumes": volumes,
        "segmentations": segmentations,
        "models": models,
        "markups": markups,
        "layout": layout,
        "slice_views": views,
    })


def _sample_sources():
    """``(category, SampleDataSource)`` for every sample a user can load."""
    import SampleData

    SampleData.SampleDataLogic()  # registers Slicer's built-in samples the first time
    for category, sources in slicer.modules.sampleDataSources.items():
        if category in HIDDEN_SAMPLE_CATEGORIES:
            continue
        for source in sources:
            yield category, source


@tool(permission=False)
def list_sample_data() -> str:
    """List the sample data sets 3D Slicer can download, by name (load one with
    load_sample_data)."""
    return json.dumps([
        {
            "name": source.sampleName,
            "description": " ".join((source.sampleDescription or source.sampleName).split()),
            "category": category,
        }
        for category, source in _sample_sources()
    ])


@tool(permission=True)
def load_sample_data(name: str) -> str:
    """Download one of 3D Slicer's sample data sets and load it into the scene. 3D Slicer
    stops responding while it downloads, and the first download of a sample can take a
    minute; after that it comes from the cache. Say so before you call it.

    Args:
        name: The sample's name, as list_sample_data gives it, for example MRHead, CTChest or CTACardio.
    """
    wanted = (name or "").strip().lower()
    source = next((s for _c, s in _sample_sources() if s.sampleName.lower() == wanted), None)
    if source is None:
        raise ValueError("Unknown sample {!r}. list_sample_data lists the ones there are.".format(name))
    import SampleData

    loaded = SampleData.SampleDataLogic().downloadFromSource(source)
    names = [node.GetName() for node in loaded if hasattr(node, "GetName")]
    if not names:
        raise RuntimeError("Loading {} failed.".format(source.sampleName))
    return "Loaded: {}.".format(", ".join(names))


@tool(permission=True)
def load_file(path: str) -> str:
    """Load a file from this computer into the scene: an image volume (.nrrd, .nii,
    .nii.gz, .mha, .mhd), a segmentation (.seg.nrrd), a model (.stl, .obj, .vtk, .vtp, .ply)
    or markups (.mrk.json). Not scene files (.mrb, .mrml) and not DICOM folders: the user
    opens those with File > Add Data or the DICOM module.

    Args:
        path: The file's full path. A leading ~ is the user's home folder.
    """
    wanted = (path or "").strip()
    if not wanted:
        # abspath("") is the working directory, which under Slicer is "/", so an empty path
        # used to be answered with "/ is a folder."
        raise ValueError("Name a file to load, with its full path.")
    full = os.path.abspath(os.path.expanduser(wanted))
    if os.path.isdir(full):
        raise ValueError("{} is a folder. Name a file; DICOM folders load through the DICOM module.".format(full))
    if not os.path.isfile(full):
        raise ValueError("There is no file at {}.".format(full))
    if full.lower().endswith(SCENE_SUFFIXES):
        raise ValueError("{} is a scene file, which would replace the user's work. The user can open it with File > Load Scene.".format(full))
    try:
        loaded = slicer.util.loadNodeFromFile(full)
    except RuntimeError as exc:
        raise ValueError("3D Slicer could not load {}: {}".format(full, str(exc).splitlines()[0]))
    nodes = loaded if isinstance(loaded, (list, tuple)) else [loaded]
    return "Loaded {}: {}.".format(
        os.path.basename(full), ", ".join(node.GetName() for node in nodes if node is not None)
    )


TITLE = "Scene & data"
TOOLS = [get_scene_context, list_sample_data, load_sample_data, load_file]
#: ``talk2view.ui.ToolPresentation`` arguments per tool: the running label the chat shows,
#: and for the tools whose calls change the user's work, the warning on the approval card.
PRESENTATION = {
    "get_scene_context": {"activity": "Reading the scene"},
    "list_sample_data": {"activity": "Listing sample data"},
    "load_sample_data": {
        "activity": "Downloading sample data",
        # Not destructive in the usual sense, but the card is the only place the user is
        # warned before a download that freezes Slicer, and a warning needs the flag.
        "destructive": True,
        "warning": "3D Slicer stops responding while it downloads, which can take a minute "
                   "the first time. You cannot stop it.",
    },
    "load_file": {
        "activity": "Loading a file",
        # The path is the model's choice, and a file on this computer can be another
        # patient's, so the card names it and says to check it.
        "destructive": True,
        "warning": "The file is read from your computer into the scene. Check the path is "
                   "the one you mean.",
    },
}
