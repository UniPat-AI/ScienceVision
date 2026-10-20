# ScienceVision scale-v2.1

Deterministic candidate rebuild from locked scale-v1/v2 inputs and local asset
bytes. It supersedes `scale-v2` as the candidate for further evaluation because
v2 retained trivial answer contracts and incorrect CT orientation/target-mask
assumptions. The locked `scale-v2` directory remains unchanged for provenance.

- Build ID: `272ea6d0457e3b78b772edfa043a78f5f75eadbe15defb5e830faef745476d5f`
- Candidates: 823 (medical 605, biology 113, chemistry 105)
- Quarantine: 510
- Source ledger: 1567 records
- Asset closure: 1307 files, 5581980948 bytes

Candidate contracts now expose the evidence needed for deterministic scoring:

- Biology collision tasks identify the model, polymer/nonpolymer atom pair,
  distance, vdW sum, overlap, and collision decision. Comparison tasks retain
  only contact margins of at least 0.5 A.
- Medical CT tasks bind one explicit `mask_id`, selected-label voxel geometry,
  affine RAS+ target coordinates, field-of-view LR reference, signed distance,
  and left/midline/right label. MRI tasks use voxel extents with tolerance.
- Chemistry binding tasks name both complete atom identities and their direct
  distance. Conformation tasks declare the ordered four-atom tuple, signed
  dihedral, and conformation class. Crystallography remains quarantined pending
  an independently verified periodic-geometry/render rebuild.

Post-build static QC classifies 605 candidates as pass, 218 as review, and 0 as
block. All six required baseline tracks remain at 0/823, so formal release is
false. Raw-coordinate computability is an access property, not evidence that a
model exploited the answer.
