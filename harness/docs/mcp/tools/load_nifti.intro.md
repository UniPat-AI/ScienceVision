## When to use

`load_nifti` is the canonical first step when working with medical volumes — call it before
any other tool that needs to know dimensions or spacing. It does **not** stream voxel data
into the agent's context (that would blow the budget for typical 512×512×512 volumes); it
returns metadata only.

If you need the voxel array itself, hand the file path to a downstream tool that operates
on the data in-place (e.g. [[compute_volume]], [[extract_segment]]).

## Example

```python
{ "tool": "load_nifti", "args": { "path": "data/kidney_ct/case-001/image.nii.gz" } }
```

Expected response:

```json
{
  "shape": [512, 512, 124],
  "spacing_mm": [0.8, 0.8, 1.0],
  "affine": [[0.8, 0, 0, -204.8], [0, 0.8, 0, -204.8], [0, 0, 1.0, -62.0], [0, 0, 0, 1.0]],
  "dtype": "int16"
}
```
