Domain: medical volumetric imaging (CT/MRI; NIfTI/NRRD volumes, segmentation
masks, referring expressions).

Grade with these domain priorities:
- Anatomical correctness: the region, organ, or lesion named must match the
  referenced anatomy in the volume; penalize laterality errors (left/right)
  and adjacent-structure confusions.
- Slice grounding: for slice/visibility tasks, full credit requires the answer
  be consistent with the slice plane and index actually inspected, not a
  generic anatomical prior.
- Segmentation overlap: when a mask is produced, spatial overlap with the
  reference is the dominant signal (see the Dice scorer); textual answers
  should describe the correct extent and location.
Partial credit for correct organ but wrong sub-region or imprecise extent.
