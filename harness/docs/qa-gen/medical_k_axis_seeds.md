# Medical K-axis Seeds — Professional Knowledge × 3D Vision for CT QA

Status: **v3 — filtered to 3D-vision-load-bearing tasks** (2026-06-05)
Scope: design contract for the K-axis seed family in the ScienceVision
QA-gen pipeline.
History: v1 design (PR #78) → v2 seed-fill realization (PR #81) →
v3 filter + reframing (this revision).

> **Reading order:** start with [`pipeline-overview.md`](pipeline-overview.md)
> for the stage / template / cell-iteration vocabulary; this file is the
> worked design for one specific seed family on top of that base.

---

## 1. What this category is — and what it isn't

The **K-axis** (knowledge-grounded spatial relations) covers a specific
class of CT QA tasks: ones that require **both** professional medical
knowledge **and** active 3D vision in the viewer, where neither alone
is sufficient.

Concretely, a task qualifies as K-axis if and only if **both** of these
are necessary to produce the answer:

- **(a) Professional medical knowledge** — anatomy naming, pathology
  mapping, staging frameworks, normal-variant ranges. This is what a
  trained radiologist brings.
- **(b) Active 3D viewer interaction** — slice navigation, window-leveled
  CT intensity reading at the mask boundary, pairwise mask-anatomy
  spatial reasoning under partial-volume averaging, multi-axis
  confirmation of (non-)invasion. This is what *cannot* be recovered
  from a single rendered thumbnail or from `mask_text` paraphrase.

If either component alone is sufficient, the task does not belong on the
K-axis.

### What this is NOT

- **Not** "open-ended medical VQA". K-axis tasks have enumerated answer
  schemas; the candidate sets are case-specific and finite.
- **Not** pure visual classification. The candidate labels carry
  clinical meaning the model must *interpret*, not just match against
  pixel patterns.
- **Not** the existing V1–V6 axis in `templates.py`. V-axis tests
  viewing-skill primitives (slice planning, visibility, annotation,
  consistency, trace, topology). K-axis tests viewing-skill **fused
  with** domain knowledge.

The V and K axes can in principle be combined cell-wise (e.g. V3-K3 =
"annotate the mask component that supports the topology answer"), but
the seeds in this document are pure-K — they specify what K alone
means.

## 2. Why the criterion matters: a tightening pass

The first attempt at this design (v1, PR #79) proposed K1–K5 — five
templates covering containment, wall-vs-lumen, multi-mask relation,
DDx ranking, and negative-containment. v2 filled the template slots
per case. Reviewing v2 in the viewer made one issue obvious: not all
five templates **needed the viewer** to be answered.

- **K1 (containment + diagnosis from a 6-option list)** — once the
  prompt names the anatomy ("aortic wall") and lists the six candidates,
  a medically-knowledgeable model can pick the answer from the mask_text
  description alone. Opening the volume does not change the most-likely
  answer.
- **K4 (DDx ranking of 4 supplied candidates)** — the ranking depends
  on demographics + finding description, not on whether the model
  navigated through axial slices.

v3 drops K1 and K4 for not passing criterion (b). The retained
templates — K2, K3, K5 — are the ones where 3D viewing is
*answer-determining*.

## 3. The K-axis seed templates (v3)

Anchored on M3D-RefSeg `datasets/_matched/m3d-refseg.json` cases
present on disk and on the strict-whitelist Tier-1 list.

### K2 — Wall-vs-lumen sub-containment

**Question shape.** *Given a high-density focus mask inside a named
vessel/organ, decide whether it sits in the wall, the lumen, or
surrounding tissue, and state the pathology that sub-location implies.*

**Why 3D viewing is load-bearing.** The same mask shape can come from
wall calcification (atherosclerosis) or intraluminal embolus
(thrombus). To distinguish, the model must window-level the CT
intensity *at the mask boundary*: a wall finding sits at the
density-transition between vessel lumen (~50 HU enhanced) and
surrounding tissue (~30 HU), and the mask edge aligns with the
high-density rim, not the lumen interior.

**Answer schema.**
```python
answer_kind="sub_location_plus_pathology"
answer_fields=("sub_compartment", "pathology_label", "evidence_slice")
answer_enums={
  "sub_compartment": ["wall", "lumen", "surrounding_tissue"],
  "pathology_label": [...case-specific list...],
}
```

**Pilot binding.** `m3d-refseg/s0146` (aortic-wall calcification →
atherosclerosis), seed answer
`{sub_compartment: "wall", pathology_label: "atherosclerosis"}`.

### K3 — Pairwise mask-anatomy topology + clinical significance

**Question shape.** *Given a labeled mask component A and a named
anatomy B, classify the spatial relation A↔B as one of
{contains, contained_by, adjacent, disjoint}, and state whether that
relation is pathologically significant.*

**Why 3D viewing is load-bearing.** Partial-volume averaging at the
mask edge makes "contains" vs "adjacent" not separable from boolean
overlap of voxel sets. The model must visually judge whether the two
components share a continuous interface (adjacent), one envelops the
other (contains/contained_by), or they sit at different anatomic
levels (disjoint). The K3 forbidden-tool list explicitly bans
`scipy_ndimage_label` and `boolean_mask_overlap_solver` to enforce
this.

**Answer schema.**
```python
answer_kind="topology_plus_significance"
answer_fields=("relation", "significance", "evidence_slice")
answer_enums={
  "relation": ["contains", "contained_by", "adjacent", "disjoint"],
  "significance": ["yes", "incidental", "no"],
}
```

**Pilot binding.** `m3d-refseg/s0212` (peritoneal nodule × sigmoid
colon wall), seed answer `{relation: "adjacent", significance: "yes"}`
(peritoneal carcinomatosis = M1c).

### K5 — Adjacent-structure invasion assessment

**Question shape.** *Given a masked lesion and a named adjacent
structure, determine whether the lesion crosses into that structure,
and map the answer to a clinical label under a stated framework
(tumor staging, infection extension).*

**Why 3D viewing is load-bearing.** A correct **NEGATIVE** answer
(no invasion) is only justified if absence-of-breach can be confirmed
on at least two orthogonal slices. A single-slice projection can hide
oblique invasion. K5 enforces `required_evidence=["variant_screenshots"]`
to make orthogonal-view confirmation a scoring gate.

**Answer schema.**
```python
answer_kind="invasion_plus_stage"
answer_fields=("invades", "stage_label", "evidence_slices")
answer_enums={
  "invades": [...case-specific binary or ternary set...]
}
```

**Pilot bindings.**
- `m3d-refseg/s0212` (peritoneal nodule × sigmoid colon wall, AJCC
  TNM peritoneal-metastasis frame), seed answer
  `{invades: "does_not_invade", stage_label: "M1c (peritoneal metastasis)"}`.
- `m3d-refseg/s1349` (inguinal high-density shadow × femoral
  neurovascular bundle, soft-tissue infection extension frame), seed
  answer
  `{invades: "contained_within_subcutaneous_layer", stage_label: "localized cellulitis without fascial breach"}`.

## 4. Pilot record set

`QA-Gen/tasks/MedicalKAxisPilot/` (set version v3):

| task_id | k_class | case | seed answer |
|---|---|---|---|
| sv-medical-k2-0004 | K2 | s0146 | wall · atherosclerosis |
| sv-medical-k3-0005 | K3 | s0212 | adjacent · yes |
| sv-medical-k5-0007 | K5 | s0212 | does_not_invade · M1c |
| sv-medical-k5-0008 | K5 | s1349 | contained_within_subcutaneous_layer · cellulitis |

Generation: `python3 QA-Gen/tasks/MedicalKAxisPilot/build_records.py`
(deterministic; 0 tokens; 0 USD).

`ground_truth.status = "auto_seed"` on all 4 records — values are
derived from `mask_text` per the K-axis builder's case_facts dict,
and require expert sign-off before promotion to `release_gate=pass`.

## 5. How the K-axis slots into the existing pipeline

The planner currently iterates cells `(domain, v_class)`
(`sciencevision_taskgen/pipeline/planner_stage.py`). To support K:

1. Add `k_classes: list[str]` to the pipeline config (e.g.
   `configs/medical_k_pilot.json`), default empty so existing runs
   are unchanged.
2. Extend the planner cell key to `(domain, v_class, k_class)`. When
   `k_class` is set, the planner looks in a sibling registry
   `K_TEMPLATES[domain]` rather than `TEMPLATES[domain]`.
3. Pure-K runs use `v_class = null`. Combined V×K runs ("annotate the
   pair that supports the topology answer") merge both templates'
   `required_evidence` and `forbidden_tools` (union).
4. Scorer primaries `k2_*`, `k3_*`, `k5_*` are new families and need
   scorer implementations. Until those land, `auto_seed` ground
   truths shipped from `build_records.py` keep the pipeline runnable
   end-to-end — the records simply stay `release_gate=blocked`.

No change is required to the **task schema**: `answer_kind`,
`answer_fields`, `required_evidence`, `scorer_primary`,
`scorer_checks` all remain free-form strings under the existing
contract.

## 6. Rejected templates (kept for transparency)

Templates that were proposed in v1 and dropped in v3 because they
fail criterion (b):

### K1 — Containment + diagnosis from a 6-option list (DROPPED)

The template proposed: *"is the masked finding contained in the named
anatomy B? Pick the clinical interpretation from {6 candidates}."*

**Why dropped.** Once the prompt names B and lists the 6 candidates,
a model with medical knowledge can match `mask_text` to the
appropriate candidate without ever opening the volume. The 3D
viewing doesn't change which candidate is most likely.

**If revisited:** K1 could be salvaged by *not* naming B in the
prompt and requiring the model to identify the container anatomy from
the rendered view. That moves answer-determination back into the
viewer. Filed as a future direction; not in v3 scope.

### K4 — Differential-diagnosis ranking of 4 supplied candidates (DROPPED)

The template proposed: *"rank 4 candidate diagnoses by likelihood,
cite one evidence slice for the top choice."*

**Why dropped.** Ranking of 4 textbook DDx for a known finding is
medical-knowledge work. The "cite one slice" requirement is a
constraint on the answer format, not on the ranking itself; the
ranking is identical whether the slice was cited or not.

**If revisited:** K4 could pivot to *"the candidates include a
visually-distinguishable pair (e.g. wall calcification vs lumen
thrombus) — rank using the visible CT density profile"*. That
brings (b) back. Filed as a future direction; not in v3 scope.

## 7. Open questions

| # | Question | Why it matters |
|---|----------|----------------|
| 1 | Should V×K cells be planned-out at task-generation time, or composed only at evaluation time? | Cross-product multiplies cell count by 3 (K2/K3/K5); could dilute the V-pilot signal. |
| 2 | K3 viewer support: per-label-id mask toggling — how? | The K3 question names component A by description. To score, the viewer must let the agent isolate one mask label. Either preprocessing splits `mask.nii.gz` per label-id into separate NIfTIs, or the viewer accepts a `selected_label_id` URL parameter. |
| 3 | K5 negatives — synthetic or real? | All current K5 records anchor on positive findings ("nodule exists, does it invade?"). To test absence-of-invasion against absence-of-finding, we need either synthetic-negative cases or external M3D-RefSeg cases curated for no-finding-in-region pairs. |
| 4 | Auto_seed acceptance threshold for promotion to `pass` | Currently 100% of v3 records carry `auto_seed` values derived deterministically from `mask_text`. Expert review can either confirm en bloc, or be required per-record; the threshold determines turnaround. |

## 8. Provenance

- Real-asset citations above are read from
  `datasets/_matched/m3d-refseg.json` records s0146, s0212, s1349 —
  strict-present-on-disk Tier-1 whitelist entries as of 2026-05-19.
- Template-shape compatibility verified against `templates.py:24-39`
  and the existing medical V1–V6 entries on lines 161–258.
- Pipeline integration sketch checked against `planner_stage.py` cell
  iteration and `README.md` "Design Contract" section.
- `ground_truth.status` enum (`auto_seed` / `awaiting_expert` /
  `needs_expert_annotation`) follows
  `sciencevision_taskgen/groundtruth/base.py:17-19` (introduced in
  PR #75).
