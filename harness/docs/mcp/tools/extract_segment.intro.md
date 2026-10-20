## When to use

Use `extract_segment` to isolate the volume of interest before downstream analysis — e.g.
running texture statistics, shape descriptors, or feeding a small CNN. It's the right move
whenever the cost of analysing the full volume is high but only a small region is relevant.

Pair it with `compute_volume` to verify the bounding box is reasonable before committing to
the crop.

## Example

```python
{
  "tool": "extract_segment",
  "args": {
    "image_path": "data/kidney_ct/case-001/image.nii.gz",
    "mask_path":  "data/kidney_ct/case-001/mask.nii.gz",
    "label": 1,
    "padding_voxels": 6
  }
}
```

Expected response:

```json
{
  "cropped_path": "/tmp/agent-scratch/case-001-label-1-cropped.nii.gz",
  "bbox_voxels": [38, 102, 14, 224, 318, 88]
}
```
