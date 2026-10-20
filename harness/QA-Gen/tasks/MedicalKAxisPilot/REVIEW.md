# MedicalKAxisPilot — Review (v3, filtered)

Pilot of the **K-axis** (knowledge-grounded spatial relations) seeds for the
ScienceVision QA-gen pipeline. See the design doc
[`docs/qa-gen/medical_k_axis_seeds.md`](../../../docs/qa-gen/medical_k_axis_seeds.md)
for the abstract template definitions and the filter criterion.

## Filter criterion (v3)

A task qualifies for this set only when **both** of these are required to
produce the answer:

- **(a) Professional medical knowledge** to interpret the masked finding
  (anatomy mapping, pathology mapping, staging frameworks).
- **(b) Active 3D vision** in the viewer: window-leveled CT density at
  the mask boundary, pairwise mask-anatomy spatial reasoning under
  partial-volume averaging, or multi-axis confirmation of (non-)invasion.

If a task can be answered from `mask_text` + the candidate list +
medical knowledge alone — without the viewer adding any
answer-determining information — it does **not** qualify and is dropped.

## What v3 dropped vs v2

| Dropped | Why |
|---|---|
| K1 ×3 (s0146 / s0212 / s0605) | Once the prompt names anatomy B (e.g. "aortic wall") and lists 6 candidate diagnoses, the answer is selectable from medical knowledge alone. The viewer becomes garnish, not load-bearing. |
| K4 ×1 (s0146) | Ranking 4 textbook DDx for "calcification in aortic wall" is a pure medical-knowledge task. The `cite one evidence slice` requirement doesn't change the ranking — the ranking is the same whether you opened the volume or not. |

## Records (v3)

| task_id | k_class | case | seed answer (auto_seed) | why 3D viewing is load-bearing |
|---|---|---|---|---|
| sv-medical-k2-0004 | K2 | s0146 | `sub_compartment=wall · pathology=atherosclerosis` | Wall vs lumen distinction requires reading window-leveled CT intensity *at the mask boundary*; the same mask shape supports atherosclerosis (wall) or thrombus (lumen) depending on intensity profile. |
| sv-medical-k3-0005 | K3 | s0212 | `relation=adjacent · significance=yes` | The pair (peritoneal nodule, sigmoid colon wall) needs visual judgement under partial-volume averaging; whether the two are `contains` vs `adjacent` cannot be determined from labels alone. |
| sv-medical-k5-0007 | K5 | s0212 | `invades=does_not_invade · stage=M1c (peritoneal metastasis)` | A negative answer (no transmural invasion) must be confirmed across at least 2 orthogonal slices; a single-plane projection is insufficient evidence. |
| sv-medical-k5-0008 | K5 | s1349 | `invades=contained_within_subcutaneous_layer · severity=localized cellulitis without fascial breach` | Same as K5-0007 but reframed for infection extension: fascial-plane crossing must be confirmed on at least 2 orthogonal slices. |

## Task IDs

Preserved from v2 (no renumbering to 0001–0004) so external references in
PRs / commit messages / earlier review notes remain valid. The gaps
(0001–0003, 0006) correspond to the dropped K1/K4 tasks.

## What reviewers should still check

1. **Auto_seed correctness.** Open each task's `asset.path` +
   `asset.mask_path` in NiiVue and confirm the seed answer matches what
   you see. Specifically:
   - **K3-0005**: the assumed pair is (peritoneal nodule, sigmoid colon
     wall). Confirm against the mask volume that the *second*-relevant
     component near the peritoneal focus is indeed sigmoid bowel wall,
     not some other component label.
   - **K5-0008**: my severity label assumes the strip-like inguinal
     shadow does not breach the fascia. Confirm — if it does breach,
     the answer flips to "crosses_fascia_into_deep_compartment" and
     severity changes to "extending into deep compartment" (which is
     not a stable enum value yet).

2. **K3 viewer support.** The K3 instruction names component A by
   description ("the peritoneal nodule mask"). The viewer doesn't yet
   expose per-label-id mask toggling. Before scoring works, either
   (a) split `mask.nii.gz` by label-id at preprocessing and pass each as
   its own NIfTI to NiiVue, or (b) extend the viewer to accept a
   `selected_label_id` URL parameter and a `sv_toggle_mask_component`
   tool stub.

3. **Scorer implementations missing.** `k2_*` / `k3_*` / `k5_*` are
   still stubs. v2 added `answer_schema.enums` so future scorers can
   do enum-match deterministically, but the actual implementations
   haven't landed.

## Provenance

- Generator: `QA-Gen/tasks/MedicalKAxisPilot/build_records.py` (v3;
  deterministic; 0 tokens, 0 USD).
- Source manifest: `datasets/_matched/m3d-refseg.json` (M3D-RefSeg
  strict-present-on-disk whitelist).
- Design doc: `docs/qa-gen/medical_k_axis_seeds.md` (introduced in PR
  #78, reframed with the v3 filter criterion).
- v1 baseline: PR #79 (template-faithful, slots unfilled).
- v2 expansion: PR #81 (per-case slot fill + auto_seed values; 8 records).
- v3 filter (this set): drops K1 ×3 and K4 ×1; keeps K2/K3/K5.
