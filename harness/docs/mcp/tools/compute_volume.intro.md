## When to use

Reach for `compute_volume` when the question is "how big is this structure?" and the structure
is delineated by a deterministic mask — typically a segmentation file paired with the source
imaging volume. Common cases:

- Comparing tumour volume before and after treatment
- Reporting organ volumes for cohort statistics
- Quantifying lesion burden in stroke imaging

Do **not** use this tool when the structure is defined only by a fuzzy / probabilistic map —
threshold it first, or pick the tool that integrates the probability mass directly.

## Example

```python
{
  "tool": "compute_volume",
  "args": {
    "mask_path": "data/kidney_ct/case-001/mask.nii.gz",
    "label": 1
  }
}
```

Expected response:

```json
{
  "volume_mm3": 142876.5,
  "voxel_count": 18241,
  "spacing_mm": [0.8, 0.8, 1.0]
}
```
