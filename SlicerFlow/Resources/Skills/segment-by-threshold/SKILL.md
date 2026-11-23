---
name: segment-by-threshold
description: Creates a segment by thresholding a volume, checks it, and refines the range. Use when the user asks to segment or threshold a volume.
---
# Segmenting by threshold

1. Call `get_scene_context` and read the volume's `value_range`.
2. Choose a range. Starting points for CT, in Hounsfield units:
   - bone: 200 to 3000 (cortical bone mostly above 700)
   - contrast-filled vessels: 150 to 500
   - soft tissue: -100 to 100
   - fat: -190 to -30
   - lung: -950 to -500
   - air: -1024 to -950

   MR has no fixed scale: choose from the value range and what the user sees.
3. Call `threshold_segment` with a clear segment name, for example `Bone`. It asks the user
   first, and it replaces a segment that already has that name.
4. Check the result: `measure_segment_volumes` gives its size, and `capture_view` lets you
   see it. A threshold also picks up everything else in the range (the CT table, other
   organs), so say so when that is likely.
5. To refine, threshold again with the same name and a narrower range.
