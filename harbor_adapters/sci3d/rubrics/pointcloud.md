Domain: point clouds (LiDAR / scanned scenes; XYZ/LAS/PLY).

Grade with these domain priorities:
- Structural grounding: object identification, counts, and spatial relations
  must come from the actual point distribution, not a scene-type prior.
- Scale & density awareness: answers about size, distance, or extent must
  respect the point cloud's scale; penalize answers that ignore sparsity or
  occlusion of the scan.
- Segmentation/selection: when a region or object is selected, its spatial
  extent must match the referenced structure.
Partial credit for correct object class but wrong count or imprecise extent.
