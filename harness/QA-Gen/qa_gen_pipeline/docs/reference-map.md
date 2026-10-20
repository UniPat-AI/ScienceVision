# Reference Map

This package owns only its new configuration, fixture, and runtime-contract
layers. Existing documents, implementations, assets, and task materials remain
at their current paths. This map records references only; it does not copy or
reinterpret their contents.

| Existing owner | New-package use | Rule |
| --- | --- | --- |
| `QA-Gen/docs/QA-gen-pipeline-vNext-plan.md` | Design authority for this package layout and safety gates. | Reference only; never runtime state. |
| External PR #221: `QA-Gen/docs/QA-gen-package-pipeline.md` | External package/pipeline design proposal. | Not part of this package or change; do not assume it exists on `main`, copy it, or import it. |
| `QA-Gen/docs/QA-gen-pipeline-target.md` | Original target context. | Reference only. |
| `QA-Gen/docs/Question-Rubric.md` | Rubric authority locked by `configs/rubric.yaml` SHA-256. | Do not copy; a digest change invalidates a compiled lock. |
| `QA-Gen/docs/PIPELINE_V2.md` | Historical implementation lessons. | Reference only; not a state-machine input. |
| `QA-Gen/docs/LLM_ROUTING_PROGRESS.md` | Historical routing record. | Reference only; not a model configuration source. |
| `QA-Gen/docs/ScienceVision_QA_Generation_Blueprint.md` | Historical generation blueprint. | Reference only; not copied into prompts or YAML. |
| Expert knowledge master `VkSxdiSTwoxwgFx4atxcHb0Qnfh`, revision 298 | Versioned source evidence for biology, chemistry, medical, and architecture leaf definitions. | `source_only`; a section citation is not an approved knowledge snapshot and cannot unlock generation. |
| Architecture/mechanical synthesis `HTpQdSZCPoaJkjxkMdPc6Rc4nIh`, revision 9 | Versioned source evidence for architecture owner mappings and all mechanical CAD directions. | `source_only`; CAD has zero approved local knowledge snapshots. |
| `QA-Gen/ScienceVisionTaskGenerator/` | Existing implementation and test reference. | Do not modify or import as the new architecture base. |
| `QA-Gen/TaskGenerator/` | Older task-generator reference. | Preserve as its own canonical owner. |
| `QA-Gen/tasks/EV2_Pilot/` | Legacy EV2 artifact reference. | Not assumed to satisfy the vNext importer package contract. |
| `datasets/ASSET_MANIFEST.json` | Existing asset-manifest owner. | Refer by path; do not duplicate assets. |
| `viewer/verify_ev2_pilot.json` | Existing viewer-verification reference. | A future adapter must independently validate it. |
| `QA-Gen/tasks/**/{assets,_inputs,_internal_assets,_gt,qc}/` | Existing assets, inputs, ground truth, and QC owners. | Never copy into this package; use permitted digest references only. |

The checked-in files in `configs/fixtures/` and `knowledge/compiled/` are
synthetic local fixtures. They exist only to exercise deterministic config
compilation and cannot authorize a production release.

Canonical catalog identities live under `configs/subdomains/`. Historical
owner keys such as `A1_drawing_model_consistency`,
`A4_section_elevation_reasoning`, and `A5_clash_overlap` are retained only in
the target leaf's `legacy_aliases`. They are lookup evidence, not additional
leaves.

Every leaf exposes `readiness.config`, `readiness.generation`, and
`readiness.release`. The schema prevents a source-only citation from being
treated as a lock: generation eligibility requires locked seed, asset, viewer,
and `knowledge-snapshot` references; release eligibility additionally requires
approved production status.
