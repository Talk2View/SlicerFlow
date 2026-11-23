---
name: ct-window-presets
description: Picks CT window/level presets for soft tissue, lung, bone and brain. Use when the user asks to change CT contrast or window/level.
---
# CT window/level presets

Apply these with `set_window_level`. They are display settings for viewing, not diagnostic
criteria. Values are in Hounsfield units.

| Tissue | Window | Level |
|---|---|---|
| Brain | 80 | 40 |
| Subdural (blood near bone) | 200 | 75 |
| Soft tissue, abdomen | 400 | 40 |
| Liver | 150 | 30 |
| Mediastinum | 350 | 50 |
| Lung | 1500 | -600 |
| Bone | 1800 | 400 |

1. Call `get_scene_context` first. A volume whose value range starts near -1024 is CT; an
   MR volume has no Hounsfield units, so these presets do not apply to it. For MR, start
   from the volume's own value range: a window of about 80% of the range, centred on it.
2. Name the volume when more than one is loaded.
3. Say which preset you used, so the user can ask for another.
