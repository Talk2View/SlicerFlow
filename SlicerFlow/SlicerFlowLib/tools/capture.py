"""``capture_view``: a screenshot the model looks at.

The tool grabs the view on the main thread and hands the PNG to the running chat's
``followup.FollowUps`` (``set_sink``), which sends it to the model in the next message
once this run is over. Without a running chat there is nowhere to send it, and the tool
says so.
"""

import os
import tempfile

import ctk
import qt
import slicer

from talk2view import tool

from SlicerFlowLib.tools import _nodes

#: The model's view names and how the follow-up message names them.
VIEWS = {
    "3d": "3D view",
    "red": "Red slice view",
    "yellow": "Yellow slice view",
    "green": "Green slice view",
    "layout": "View layout",
}
#: Larger screenshots are scaled down to this many pixels on their longest side.
MAX_SIDE = 1600

def set_sink(sink):
    """``sink(png_bytes, label) -> str`` receives each screenshot (None: no chat)."""
    _nodes.screenshot_sink = sink


def _widget(key):
    layout = slicer.app.layoutManager()
    if key == "3d":
        return layout.threeDWidget(0).threeDView()
    if key == "layout":
        return layout.viewport()
    return layout.sliceWidget(_nodes.SLICE_VIEWS[key]).sliceView()


def grab_png(widget):
    """The widget as PNG bytes, at most ``MAX_SIDE`` pixels on its longest side."""
    image = ctk.ctkWidgetsUtils.grabWidget(widget)
    if max(image.width(), image.height()) > MAX_SIDE:
        image = image.scaled(MAX_SIDE, MAX_SIDE, qt.Qt.KeepAspectRatio, qt.Qt.SmoothTransformation)
    handle, path = tempfile.mkstemp(suffix=".png")
    os.close(handle)
    try:
        if not image.save(path, "PNG"):
            raise RuntimeError("The screenshot could not be saved.")
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        os.remove(path)


@tool(permission=True)
def capture_view(view: str = "3d") -> str:
    """Take a screenshot of a view to look at it yourself, for example to check how a
    segmentation or a volume rendering looks. You get the image in the next message, after
    this reply. Slice views can show patient details in their corner text.

    Args:
        view: 3d, red, yellow, green, or layout (every view, as the user sees them).
    """
    sink = _nodes.screenshot_sink
    if sink is None:
        raise ValueError("Screenshots need the SlicerFlow chat panel's chat to be running.")
    key = _nodes.choose(view, list(VIEWS), "view")
    widget = _widget(key)
    if widget is None or not widget.isVisible():
        raise ValueError("The {} is not in the current layout. Change the layout first (set_layout).".format(VIEWS[key]))
    return sink(grab_png(widget), VIEWS[key])


TITLE = "Screenshots"
TOOLS = [capture_view]
#: ``talk2view.ui.ToolPresentation`` arguments per tool (see ``scene.PRESENTATION``).
PRESENTATION = {
    "capture_view": {
        "activity": "Taking a screenshot",
        "destructive": True,
        "warning": "The screenshot is sent to Talk2View. Slice views can show patient details.",
    },
}
