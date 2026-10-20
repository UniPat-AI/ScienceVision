# ScienceVision scale-v2 QC candidate set

This directory is a candidate data drop, not a formal benchmark release.
It preserves the deterministic output of the QC-driven scale-v1 repair build,
including candidate tasks, quarantined tasks, the per-task decision ledger, and
the build manifest that hashes every generated file.

## Data status

- Source tasks reviewed: 1,567.
- Repaired candidates: 986 (medical 839, biology 147, chemistry 0).
- Quarantined tasks: 581 (biology 180, chemistry 401, medical 0).
- Initial post-repair static QC: 839 pass, 147 review.
- Round2 materialization QC: 260 pass, 126 review, 600 block.
- Required six-baseline coverage: 0 / 986 candidates.
- Formal release: not granted.

The initial static result and the round2 result measure different gates. The
first checks repaired contracts and static shortcut evidence. Round2 also
checks target binding, asset availability, multi-asset staging, and materialized
geometry. Neither result includes the required baseline evaluation.

## Provenance

- ScienceVision source commit:
  `662bdb7b7735816a8cb7e6639b36ddf908972752`
- Build manifest: `scale-v2-build.json`
- Build manifest SHA256:
  `9e8b171065f8ef862dc7302191ec7e665c513f7838cad2376b3bb134c7c5b943`
- Reviewed scale-v1 source lock:
  `docs/qa-gen/SCALE_V1_REVIEWED_20260808_LOCK.json`
- External scale-v2 build lock:
  `docs/qa-gen/SCALE_V2_BUILD_20260808_LOCK.json`

The candidate and quarantine files must remain disjoint and complete relative
to the 1,567-row decision ledger. The hashes and row counts in the build
manifest are authoritative for this data drop.

The build manifest records the ScienceVision schema hash used to create the
original artifacts. BabyVision-v2 maintains an independently evolved canonical
schema, so its schema file is intentionally not byte-identical. This import adds
only the required domain, structured-answer, and grounding compatibility, and
all 1,567 candidate and quarantine rows are validated against the resulting
BabyVision-v2 schema.

## Promotion conditions

Before any task is registered in a release or viewer index:

1. Resolve all round2 materialization blockers, including target-mask binding,
   union-mask geometry, missing assets, and unsupported multi-asset staging.
2. Rerun static and materialization QC with no unresolved blocking findings or
   quarantine-reason drift.
3. Complete all six required baselines for every promoted task and run targeted
   shortcut probes where the QC record identifies a candidate route.
4. Publish a separate promotion manifest that explicitly grants formal release.

Until those conditions are met, downstream consumers must treat every file in
this directory as review data rather than a production benchmark release.
