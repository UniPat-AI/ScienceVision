# Question Family and GT Redesign

Status: design specification and review input. This document does not enable a
subdomain, approve a ground truth, or alter a release decision.

The local typed source artifacts under
`qa_gen_pipeline/configs/sources/feedback/v3_2_expert_feedback/accepted/`
are accepted **compile inputs** for their named leaves. `accepted` only means
that the feedback parser may bind typed rules into a draft knowledge snapshot.
Every artifact is explicitly non-production. It does not turn the M3D-RefSeg
source declaration, a segmentation, an expert review, or a medical leaf into
approved ground truth.

This redesign responds to the v3.2 feedback triage in
`v3.2-expert-feedback-triage.md`. Its central rule is simple: a domain label
is not enough. The question must require a domain-relevant property,
attribute, or comparison that is visible in the controlled viewer and tied to
approved ground-truth provenance.

## Portfolio Rule

For every domain coverage plan, counted over the planned items in that domain:

- measurement questions: at most 25 percent;
- property, attribute, and comparison questions combined: at least 75 percent;
- a measurement may support a scientific judgment, but a naked distance,
  bbox, centroid, voxel count, or axis calculation is not sufficient domain
  content;
- every item declares one `question_family`, one `gt_provenance`, and one
  required reviewer role before any author call.

The `25` and `75` values are hard portfolio bounds from the current-thread
product direction, not a quotation from the Feishu workbook. They apply to
biology, chemistry, and medical independently, as well as to any combined
cross-domain portfolio. Exact integer allocation for a smaller run must still
preserve both inequalities after rounding.

## Domain Matrix

This matrix is a planning policy, not a claim that every listed family is
currently enabled. A row is usable only after its asset, viewer, source, and
review gates are present.

| Domain | Primary non-measurement families | Authoritative GT source types | Measurement boundary |
| --- | --- | --- | --- |
| Biology | Docking-pose plausibility, interaction attributes, local collision/property comparisons, multi-structure comparisons | `expert_annotation`, `curated_source_label` with locked viewer identifier mapping, `rule_evaluated_structure`, `paired_comparison` | A viewer-native receipt may support a named scientific claim; it cannot be the claim by itself. |
| Chemistry | Chirality, conformation class, steric accessibility, coordination geometry, crystal-packing and reactive-conformation comparisons | `curated_source_label`, `expert_annotation`, `rule_evaluated_structure`, `paired_comparison` | Distance, angle, or dihedral-only prompts are measurement items and remain within the 25-percent cap. |
| Medical | Anatomical or lesion localization, finding/location/impression semantics, morphology, imaging-feature assessment, spatial relation | `curated_source_label` only when a locked semantic record exists, `expert_annotation`, `paired_comparison`; `measurement_receipt` only as subordinate evidence | The target must first be located in the unmasked primary image; hidden-mask measurement is conditional and never establishes clinical truth alone. |

## Shared Candidate Contract

Every candidate family uses the following conceptual contract. Field values
must be frozen before authoring; an agent can format approved evidence but
cannot create a missing answer or label.

```json
{
  "question_family": "lowercase-family-id",
  "question_kind": "property|attribute|comparison|measurement",
  "answer": {"schema": "domain-specific typed answer"},
  "ground_truth": {
    "provenance_type": "expert_annotation|curated_source_label|rule_evaluated_structure|paired_comparison|measurement_receipt",
    "source_digest": "sha256:<locked-source>",
    "evidence_refs": ["immutable-reference"],
    "expert_approval_required": true
  },
  "uncertainty": {"mode": "exact|ordinal|set|calibrated-tolerance"},
  "review": {"required_role": "domain-expert"}
}
```

`model_only`, an LLM rationale, an answer copied from a prompt, or a
post-hoc author calculation is never a GT provenance type. A
`measurement_receipt` is valid only for an already approved viewer-visible
measurement and cannot establish a latent clinical, chemical, or biological
property on its own.

## Solver-Facing Boundary

The solver-facing prompt names the scientific or clinical object to judge, the
observable claim, and the answer form. It does not teach the solver how the
viewer is implemented or how an author obtained the reference answer.

- Forbidden prompt content includes directives such as `use/open/select the
  viewer`, `rotate/zoom the scene`, `select an overlay`, or `inspect the
  centroid/bounding box`; mask labels, FOV, voxel axes/indices, metadata, and
  private identifiers are likewise not task content.
- A permitted viewer operation is recorded in the private session contract and
  evidence receipt. It is not described as a solver instruction. The verifier
  rejects UI/meta-language even when the underlying scientific question is
  otherwise valid.
- A clinical wrapper cannot repair a geometry-only question. In particular, a
  medical prompt fails when removing the primary image while retaining a mask,
  overlay, or geometry computation leaves the answer unchanged.
- `question_text_policy` scans run before review and release; the author and
  rewriter receive the same restriction, while the verifier remains
  independent of their output.

## Biology

| Family | Kind | Natural solver-facing question example | GT answer schema and provenance | Allowed uncertainty | Required reviewer |
| --- | --- | --- | --- | --- | --- |
| Docking-pose plausibility | Property | `在该复合物中，配体的取向更支持稳定结合还是明显的局部冲突？请指出决定判断的关键接触区域。` | `{classification: plausible|implausible, supporting_contacts: [stable-viewer-id]}`; `expert_annotation` or `rule_evaluated_structure` with locked interaction definition | Categorical answer; multiple supporting contacts allowed only when the expert record says they are equivalent | Structural biologist or docking specialist |
| Interface interaction attribute | Attribute | `界面中哪一类相互作用最能解释这两个组分在该区域的贴合关系？` | `{interaction_class, participant_set}`; `expert_annotation` or curated structure record with explicit ID mapping | Closed controlled vocabulary; no inferred atom identity | Structural biologist |
| Local collision/property comparison | Comparison | `比较两个候选接触区域，哪一个更符合已定义的非冲突界面关系？` | `{preferred_region_id, rationale_class}`; `paired_comparison` plus expert annotation | Ties only if explicitly annotated | Structural biologist |
| Multi-structure conformation comparison | Comparison | `与状态 A 相比，状态 B 在该区域表现出哪一种构象变化？` | `{change_class, affected_region_ids}`; `paired_comparison` over registered structures | Registration uncertainty must be stored; no answer if alignment evidence is absent | Structural biologist |
| Structural attribute | Attribute | `该结构区域最符合哪一种已注释的构象或界面属性？` | `{attribute_label, evidence_region_ids}`; `expert_annotation` or `curated_source_label` | One approved label or approved equivalent-label set | Structural biologist |

Measurement can be attached as a supporting receipt for a named contact or
conformational change, but it remains within the 25-percent measurement budget
and cannot ask the solver to find an invisible centroid, xyz coordinate,
PCA axis, or author-only identifier.

## Chemistry

| Family | Kind | Natural solver-facing question example | GT answer schema and provenance | Allowed uncertainty | Required reviewer |
| --- | --- | --- | --- | --- | --- |
| Chirality | Attribute | `图中标记的立体中心属于哪一种已定义构型？` | `{stereocenter_id, configuration}`; `curated_source_label` or `expert_annotation` | Equivalent symmetry-related labels only when the curated record declares them | Organic or stereochemistry expert |
| Conformation class | Attribute | `该分子当前构象最符合哪一类已定义的构象状态？` | `{conformer_class, defining_feature_ids}`; `curated_source_label` or `rule_evaluated_structure` | Ordinal/closed class only; no raw atom number answer unless viewer mapping is locked | Computational or physical organic chemist |
| Steric accessibility | Property | `在两个可接近方向中，哪一个更符合已注释的可接近反应面？` | `{preferred_face_or_site, accessibility_class}`; `expert_annotation` or `paired_comparison` | Tie only when approved evidence records a tie | Organic chemistry expert |
| Coordination geometry | Attribute | `中心原子的局部配位环境最符合哪一种几何类型？` | `{center_id, geometry_class, ligand_set}`; `expert_annotation` or `rule_evaluated_structure` | Closed geometry vocabulary with an explicit distortion/ambiguous state | Inorganic chemistry expert |
| Crystal packing | Comparison | `比较这两个晶体接触关系，哪一个符合已注释的主要堆积模式？` | `{preferred_contact_class, relation_ids}`; `curated_source_label` or `paired_comparison` | Requires verified periodic context; otherwise family is blocked | Crystallographer |
| Reactive conformation | Property, conditional | `在已验证的反应中间体中，哪一种构象关系支持给定的立体结果？` | `{reactive_conformation_class, outcome_relation}`; `expert_annotation` plus curated reaction record | Only enabled when mechanism and outcome provenance are locked | Synthetic chemistry expert |

Distances, angles, and dihedrals may be supporting evidence for the above
families. A question that asks only for the number remains a measurement item,
not a chemistry property item.

## Medical

### Modeling Boundary

For medical data, **subdomain means question type**, not an asset bucket.
Asset facets are independent coverage dimensions and must not silently become
the answer. At minimum, a medical asset record separates modality, body
region, organ/anatomical structure, dataset/source, study/series identity,
finding class, label identity, and timepoint when present.

The solver never receives a segmentation mask or overlay. A mask can exist
only as a hidden reference for GT generation or review. The M3D-RefSeg doctor
description can serve as semantic GT for `finding`, `location`, and
`impression` only after an immutable source record, verbatim provenance, and
required expert approval are locked. Its current source declaration is not an
imported annotation corpus, so it must not be used to synthesize a location,
an organ, or a diagnosis. Even after import, it remains limited to its original
annotation granularity.

For MSD Task04 hippocampus, label 1 (anterior) and label 2 (posterior) retain
their identity. They must not be merged into one target. Coverage accounting
uses the canonical key `(dataset, normalized asset facets, question kernel)`;
35 near-identical items from one dataset therefore count as one coverage
bucket rather than broad medical coverage.

| Family | Kind | Natural solver-facing question example | GT answer schema and provenance | Allowed uncertainty | Required reviewer |
| --- | --- | --- | --- | --- | --- |
| Anatomical localization | Attribute | `图中所示异常主要位于哪一解剖区域？` | `{anatomical_region, evidence_region}`; `curated_source_label` or `expert_annotation` tied to the visible study | Approved hierarchy-aware label set; no invented organ name | Radiologist or relevant clinician |
| Lesion localization | Attribute | `该异常相对于已识别的解剖结构位于何处？` | `{target_location, spatial_relation}`; doctor annotation/report or expert annotation | Coarse region/side permitted when source granularity is coarse | Radiologist |
| Finding/location/impression semantics | Property | `该影像中已标注的发现、所在位置和印象分别是什么？` | `{finding, location, impression}`; immutable `curated_source_label` from the doctor description | Each field may be `not_stated`; never infer a more specific diagnosis | Radiologist |
| Morphology | Property | `该异常的边界和内部形态最符合哪一组已注释特征？` | `{margin_class, internal_feature_class}`; expert annotation or curated report label | Closed vocabulary plus `indeterminate` if the expert record allows it | Radiologist |
| Imaging feature assessment | Property | `图中哪一种影像学特征最支持该已注释的发现？` | `{feature_class, evidence_region}`; `expert_annotation` or `curated_source_label` | Equivalent feature set only when source says equivalent | Radiologist |
| Spatial relation | Comparison | `该异常与相邻结构的关系更符合哪一种已注释描述？` | `{relation_class, reference_structure}`; expert annotation or paired comparison | Requires a visible reference structure; otherwise blocked | Radiologist |
| Localize-then-measure | Attribute/property with subordinate measurement, conditional | `该异常位于何处，其最大径属于哪个经校准的范围？` | `{location, size_interval}`; hidden reference mask plus `measurement_receipt`, with clinical/expert approval | Broad, calibrated tolerance; never exact voxel/bbox scoring | Radiologist and measurement-protocol owner |
| Diagnosis | Property, conditional | `在影像表现和已提供的临床印象共同支持下，哪一项结论与记录一致？` | `{diagnostic_impression}`; explicit doctor impression or approved clinical label | Only the recorded conclusion; no free-form differential generation | Radiologist or diagnosing clinician |

Diagnosis is disabled unless a locked doctor impression or approved clinical
label exists. A mask, a geometry measurement, or a model-generated rationale
cannot enable it. Likewise, a localize-then-measure question is disabled if
the target cannot first be located in the unmasked image. Its hidden reference
mask may be consulted only after localization, and scoring must use a
clinically calibrated tolerance rather than exact voxel, centroid, or bbox
geometry. The candidate's top-level family remains an image-grounded
localization/property family; a measurement-only medical candidate is
rejected even when it has a calibrated measurement receipt.

## Review and Coverage Gates

Before a plan becomes executable, the owner records:

1. family counts and the 25/75 portfolio check;
2. independent asset facets and a duplicate-coverage key;
3. immutable GT provenance for every candidate;
4. allowed uncertainty and the named expert reviewer;
5. controlled-viewer evidence proving the required observation is visible;
6. a no-tool-language and no-metadata-shortcut scan of solver-facing text.

Any missing item blocks the family. This prevents a fluent agent from filling
the gap with an invented answer, a hidden mask label, or a generic template.
