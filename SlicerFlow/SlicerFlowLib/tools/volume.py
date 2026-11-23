"""Tools that change how a volume looks."""

import slicer

from talk2view import tool

from SlicerFlowLib.tools import _nodes

#: Named in the tool's description; any preset Slicer has is accepted.
COMMON_PRESETS = ["CT-Chest-Contrast-Enhanced", "CT-Bone", "CT-AAA", "CT-Cardiac3", "MR-Default"]


@tool(permission=False)
def set_window_level(window: float, level: float, volume_name: str = "") -> str:
    """Set the display window and level (contrast and brightness) of a volume.
    Typical CT values: soft tissue window 400 level 40, lung window 1500 level -600,
    bone window 1800 level 400.

    Args:
        window: The window width. Greater than 0.
        level: The window center.
        volume_name: The volume to change. Empty: the volume shown in the Red view.
    """
    if window <= 0:
        raise ValueError("The window must be greater than 0.")
    volume = _nodes.volume(volume_name)
    if volume.GetDisplayNode() is None:
        volume.CreateDefaultDisplayNodes()  # a volume made in code has none until now
    display = volume.GetDisplayNode()
    if display is None:
        raise ValueError("{} has no display node, so it has no window and level.".format(
            volume.GetName()))
    display.AutoWindowLevelOff()
    display.SetWindowLevel(window, level)
    return "Window {} / level {} on {}.".format(window, level, volume.GetName())


@tool(permission=False)
def set_volume_rendering(visible: bool = True, preset: str = "", volume_name: str = "") -> str:
    """Show or hide the 3D volume rendering of a volume, optionally with a preset.

    Args:
        visible: True to show the volume rendering, False to hide it.
        preset: Optional preset, for example CT-Chest-Contrast-Enhanced, CT-Bone, CT-AAA, CT-Cardiac3 or MR-Default.
        volume_name: The volume to render. Empty: the volume shown in the Red view.
    """
    volume = _nodes.volume(volume_name)
    logic = slicer.modules.volumerendering.logic()
    # Before CreateDefaultVolumeRenderingNodes, which adds nodes to the user's scene: a
    # preset the model guessed wrong must refuse without leaving any behind.
    preset_node = logic.GetPresetByName(preset) if preset else None
    if preset and preset_node is None:
        raise ValueError("Unknown preset {!r}. Common ones: {}.".format(preset, ", ".join(COMMON_PRESETS)))
    display = logic.GetFirstVolumeRenderingDisplayNode(volume)
    if display is None:
        display = logic.CreateDefaultVolumeRenderingNodes(volume)
    if preset_node is not None:
        display.GetVolumePropertyNode().Copy(preset_node)
    display.SetVisibility(bool(visible))
    if visible:
        slicer.app.layoutManager().resetThreeDViews()
    return "Volume rendering of {} {}{}.".format(
        volume.GetName(), "shown" if visible else "hidden", " with " + preset if preset else "")


TITLE = "Volumes"
TOOLS = [set_window_level, set_volume_rendering]
#: ``talk2view.ui.ToolPresentation`` arguments per tool (see ``scene.PRESENTATION``).
PRESENTATION = {
    "set_window_level": {"activity": "Adjusting window/level"},
    "set_volume_rendering": {"activity": "Updating volume rendering"},
}
