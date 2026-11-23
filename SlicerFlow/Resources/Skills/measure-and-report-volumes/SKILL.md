---
name: measure-and-report-volumes
description: Measures segmentation volumes and reports them clearly and cautiously. Use when the user asks for a segment's volume or size.
---
# Measuring and reporting volumes

1. Call `get_scene_context` to see the segmentations and their segments.
2. Call `measure_segment_volumes`, naming the segments the user asked about.
3. Report each volume in cm3 with one decimal place (1 cm3 is 1 mL), in a small table when
   there is more than one.
4. Say how it was measured: by counting the segment's voxels, so it depends on how the
   segment was made and on the volume's voxel size.
5. Do not interpret a volume as normal or abnormal, and do not diagnose. If the user asks
   what a value means for a patient, suggest they discuss it with a qualified clinician.
