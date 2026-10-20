# ScienceVision scale-v2.1 build

`scale-v2.1` is a deterministic candidate rebuild. It leaves the locked
`QA-Gen/tasks/scale-v2/` dataset unchanged and publishes to
`QA-Gen/tasks/scale-v2.1/` only after source locks, assets, task schemas,
oracle receipts, and the source-task ledger pass.

## Build

```bash
uv run --no-project --isolated --python 3.12 \
  --with gemmi==0.7.5 \
  --with nibabel==5.3.2 \
  --with numpy==2.5.1 \
  --with rdkit==2026.3.5 \
  --with jsonschema \
  python -m scripts.qa_gen_scale_v2_1 \
  --asset-root /path/to/scale-v2-expert-review-candidate986-20260810
```

The asset root is never emitted. Tasks contain repository-relative paths,
SHA-256 hashes, and byte sizes. Re-running with the same locked inputs and
asset bytes produces the same build ID and output bytes.

## Candidate partition

| Domain/family | Tasks |
|---|---:|
| Biology: robust nonpolymer/polymer clash geometry | 97 |
| Biology: comparison with contact margin >= 0.5 A | 16 |
| Medical: unique CT target-label localization/geometry | 345 |
| Medical: locked MRI voxel extent | 260 |
| Chemistry: confirmed explicit atom distance | 23 |
| Chemistry: explicit dihedral, class margin >= 10 deg | 82 |
| **Total** | **823** |

The quarantine contains 510 tasks. The 1,567-record ledger maps every
scale-v1 source task, including all 579 CT rows that collapse to 345 unique
`(mask asset, mask_id)` target contracts. No source task is silently dropped.

## Oracle contracts

- Collision uses model 0 asymmetric-unit Cartesian coordinates, occupied
  non-water PDBx NonPolymer and Polymer heavy atoms, deterministic altloc
  collapse, direct `_struct_conn` exclusion, Gemmi vdW radii, and a 0.4 A
  overlap threshold. Scale-v1 binary GT is not reused.
- CT joins every source qid to `datasets/_matched/m3d-refseg.json`, whose
  `extra.mask_id` preserves the upstream CSV `Mask_ID`. Foreground is exactly
  `mask == mask_id`; other nonzero labels are excluded from rendering, oracle
  computation, and scoring. Voxel coordinates are mapped with the CT NIfTI
  affine to RAS+ millimetres. Signed LR distance is measured from the CT
  field-of-view RAS-x midpoint; it is not called a body midline.
- Binding candidates name both complete atom instances and score their direct
  distance. No angle is introduced without a topology-backed three-atom
  contract.
- Conformation candidates use one-based XYZ line IDs in an explicit ordered
  four-atom tuple. RDKit connectivity and strict rotors are selected without
  consulting old GT. The signed angle uses an ordered atan2 convention.
- Crystallography remains quarantined until a replacement periodic-geometry
  family has an independently verified periodic-render receipt.

## CT target audit

The 193 selected M3D cases each contributed three scale-v1 rows. They are not
three interchangeable copies per case: the matched index resolves 345 target
labels. There are 188 targets referenced once, 80 referenced twice, and 77
referenced three times; duplicate rows for the same target agree on mask text
and old GT. All 345 selected labels exist in the NIfTI masks. Actual masks have
1 to 14 nonzero labels. Case `s0021` has observed labels `[2, 3, 4]` while the
registry's `mask_label_count` is 4 because that field counts `text.json`
entries, not labels physically present in the NIfTI; both selected labels 2
and 4 are present.

The old scale-v1 laterality values do not survive the corrected RAS+ target
calculation: only 54 of 345 target contracts match exactly. Among the 184 old
non-midline targets, only 54 keep the same left/right direction. The former
all-nonzero union result also disagrees with the selected target for 106 of 345
contracts, which is why whole-mask deduplication is prohibited.

## Release status

The checked-in build is a candidate artifact, not a formal release. Task
schema, source lock, asset closure, and ledger partition gates pass. The
authoritative post-build QC outputs are ScienceVision source-build artifacts:

- Standard QC: `outputs/qa-qc/scale-v2.1-reviewed-20260810/`
- Final round2 materialization QC:
  `outputs/qa-qc/scale-v2.1-round2-final-20260810/`

Those paths are relative to the ScienceVision source-build workspace. The QC
reports, source payloads, generated asset bundles, and local build assets are
intentionally not mirrored into BabyVision; this repository retains the
candidate tasks, source lock, build manifest, ledger, and oracle receipts
needed to verify their provenance and partition.

Final round2 classifies 605 tasks as pass, 218 as review, and 0 as block. It
reports 0 materialization findings; all 510 quarantine reasons reconcile with
the source ledger and oracle receipts. All six required baseline tracks remain
at 0/823 coverage. Consequently `scale-v2.1` remains candidate-only and must
not be presented as a release. Raw-coordinate computability is recorded as an
access property and is not itself treated as observed answer exploitation.
