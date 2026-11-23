---
name: explore-sample-data
description: Lists which 3D Slicer sample data sets suit which demonstration, and a good first view of each. Use when the user wants to load or explore sample data.
---
# Exploring the sample data

Call `list_sample_data` for the full list; `load_sample_data` asks the user first.

- **MRHead**: a brain MRI. Conventional layout; try volume rendering with `MR-Default`.
- **CTChest**: a chest CT. Lung window (1500 / -600), then bone (1800 / 400); volume
  rendering with `CT-Chest-Contrast-Enhanced`.
- **CTACardio**: a contrast CT of the heart. Volume rendering with `CT-Cardiac3`, and a
  threshold of about 150 to 500 for the contrast-filled vessels.
- **MRBrainTumor1** and **MRBrainTumor2**: brain MRIs with a tumour, good for comparing two
  volumes (the dual-3d layout).

After loading, call `center_views`, and tell the user what they are looking at.
