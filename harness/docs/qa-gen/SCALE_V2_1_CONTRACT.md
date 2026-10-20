# ScienceVision scale-v2.1 contract

`scale-v2.1` is a candidate build, not a released benchmark. Promotion requires
post-build static QC and baseline evaluation in addition to the builder's asset,
schema, and partition gates. The locked `scale-v2` directory is an input and is
never rewritten by the v2.1 builder.

## Deterministic answer contract

Candidate tasks use `structured_deterministic_rubric_v1` with
`scorer_config.schema_version = sciencevision.generic_deterministic_rubric.v1`.
The runtime accepts exactly one JSON object with exactly the declared answer
fields. The terminal marker is `SCI3D_FINAL_JSON:`. Unknown versioned scorers,
missing ground truth, invalid rubric fields, extra answer fields, and unavailable
required assets fail closed with an `answer_score` of zero. Deterministic tasks
do not acquire an implicit LLM judge, and their formal Harbor reward is exactly
`answer_score`; process signals remain diagnostic.

## Access tracks

Raw compute-enabled evaluation and rendered viewer-only evaluation are separate
tracks. A raw-file route may compute geometry from authorized local bytes. A
viewer-only route may consume only rendered evidence and the declared viewer
controls. Results from one track must not be presented as results from the other.

Grounding receipts record the intended target. For labeled NIfTI masks, an
explicit `mask_id` selects one label. A declared union/all-nonzero foreground
policy keeps all nonzero labels; the runtime must not silently substitute the
first label. Model viewer routes exclude ground truth, QC records, scorer
configuration, receipts, and provenance. Expert routes expose those fields for
review.

## Promotion gates

- Asset hash closure, schema validation, and source-ledger partition must pass.
- Candidate and quarantine outputs must account for every locked source task.
- Post-build static QC and both access-track baselines must complete.
- Promotion creates a separately reviewed release; candidate status alone is
  not sufficient.
