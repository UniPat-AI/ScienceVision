# Mirror manifest

[`.mirror-include`](.mirror-include) is the authoritative list of paths mirrored
from [`SueMarsR/Science-Vision`](https://github.com/SueMarsR/Science-Vision).
The QA-Gen vNext package, reviewed design inputs, and controlled viewer bridge in
this checkout are pinned to ScienceVision `main` merge commit
[`b652322`](https://github.com/SueMarsR/Science-Vision/commit/b652322486e4a161875f9fa0d88d002c7f64b471).
That tree includes the controlled-generation change
[`773a93d`](https://github.com/SueMarsR/Science-Vision/commit/773a93da49be3eb4962301d2bbc5bf12ebde0ead)
and the solver-facing Mol* provenance-removal fix
[`5fe6676`](https://github.com/SueMarsR/Science-Vision/commit/5fe66763ef75c453f9a40a2e66d5939028d0d6db).

The local sync implementation is
[`tools/sync_from_sciencevision.py`](tools/sync_from_sciencevision.py). Mirroring
is still an explicit maintainer operation; the `qagen-vnext` workflow validates
the mirrored result but does not fetch or sync the source repository.

## Running a sync locally

```bash
# Dry-run: review both copies and deletions before changing the mirror.
python tools/sync_from_sciencevision.py --source ../ScienceVision --dry-run

# Apply only after the dry-run is understood.
python tools/sync_from_sciencevision.py --source ../ScienceVision
```

The source checkout defaults to `../ScienceVision` and can be changed with
`--source`. The sync preserves source-relative paths. It prunes repository and
tool caches, including `.qagen`, before planning copies or deletions.

The full manifest aggregates independently sourced mirror batches, so verify
and run a pinned update with a batch-specific manifest. For the current vNext
batch:

```bash
mkdir -p .local
cat >.local/qagen-vnext.mirror-include <<'EOF'
QA-Gen/qa_gen_pipeline/**
QA-Gen/docs/Question-Rubric.md
QA-Gen/docs/QA-gen-pipeline-vNext-plan.md
QA-Gen/docs/QA-gen-pipeline-target.md
QA-Gen/docs/question-family-gt-redesign.md
QA-Gen/docs/v3.2-expert-feedback-triage.md
viewer/shared/molstar-mcp-adapter.js
tests/viewer/test_molstar_mcp_adapter.cjs
EOF

python tools/sync_from_sciencevision.py \
  --source ../ScienceVision \
  --manifest .local/qagen-vnext.mirror-include \
  --expected-source-commit b652322486e4a161875f9fa0d88d002c7f64b471 \
  --dry-run
```

Remove `--dry-run` only after reviewing the scoped plan. The commit guard is
optional for backward-compatible path syncs, but a pinned batch must use it.
It rejects abbreviated SHAs, non-Git source directories, a source path that is
not the worktree root, any HEAD mismatch, and any staged, tracked, or ordinary
untracked source change. Git-ignored build outputs may exist only when the sync
prune rules exclude them. Any remaining manifest-matched source file must be
tracked by the expected commit, so an ignored `.env` inside a recursive mirror
scope is rejected. Use a clean detached worktree at the pinned commit when
invoking the guard. An alternative is to materialize the exact commit with
`git archive` and sync that plain directory without the Git-only guard,
retaining a separate blob/content verification record. Never copy from a dirty
checkout. Do not apply the vNext pin to the full `.mirror-include`; validate
each independently pinned batch separately.

## Mirrored boundaries

- Agent, viewer, matching, SAM-service, MCP-test, and selected documentation
  paths remain governed by their existing manifest entries.
- The legacy QA generator, scripts, task batches, and 3D metadata indexes retain
  their existing entries and ownership.
- QA-Gen vNext is mirrored as `QA-Gen/qa_gen_pipeline/**`, together with the
  five explicitly listed design and feedback documents under `QA-Gen/docs/`.
  Its controlled viewer capability evidence is the source-pinned static bridge
  under `viewer/shared/viewer-mcp-api.js` and the Mol*/NiiVue adapters. The
  Mol* state regression is mirrored explicitly and verifies that source URLs,
  filenames, asset IDs, and formats never cross the solver-facing MCP boundary.
- Raw datasets and internal coordination material remain outside the mirror.

The nested vNext project is a separate Python distribution. Install the root
project and vNext package independently:

```bash
python -m pip install -e ".[dev]"
python -m pip install -e "QA-Gen/qa_gen_pipeline[dev]"
```

The root distribution owns the `babyvision` console script. The nested
`sciencevision-qa-gen` distribution owns `qa-gen`; it does not replace or
extend the root package.

## Ownership and runtime state

ScienceVision owns the `qagen` implementation. BabyVision is a mirror and
consumer. The legacy `QA-Gen/ScienceVisionTaskGenerator`, existing
`QA-Gen/tasks/`, `data/qagen_index.json`, viewer data/indexes, and Harbor
adapters remain under their current owners. Mirroring vNext does not authorize
the new package to rewrite any of them.

Never mirror or commit `.qagen/`, provider credentials or approvals, private
seed/knowledge snapshots, audit bundles, release staging, or other runtime
outputs. The sync tool excludes `.qagen` even though the package itself is
included recursively.

Do not use `QA-Gen/qa_gen_pipeline/` as a runtime `--workspace`. The package
ships a fixture-format `knowledge/index.yaml`, while runtime knowledge uses a
different schema at the same relative path. A package-root workspace would
therefore overwrite or misread checked-in fixture data. Use an external,
gitignored location such as:

```bash
qa-gen plan compile --workspace .local/qagen-workspaces/example ...
```

The repository already ignores `.local/`. A directory outside this checkout is
also suitable.

If this summary and `.mirror-include` disagree, the manifest wins. The detailed
consumer boundary is documented in
[`docs/qa-gen/vnext-integration-boundary.md`](docs/qa-gen/vnext-integration-boundary.md).
