# QA-Gen vNext integration boundary

## Authority and scope

ScienceVision owns the `qagen` package. BabyVision consumes an explicitly
pinned mirror of that package; it is not a second implementation or an
independent source of truth. The current mirror is pinned to ScienceVision
`main` merge commit `b652322486e4a161875f9fa0d88d002c7f64b471` (which includes
the controlled-generation change `773a93da49be3eb4962301d2bbc5bf12ebde0ead` and
the anonymous Mol* viewer repair `5fe66763ef75c453f9a40a2e66d5939028d0d6db`).
The mirrored files are
copied from that Git tree, not from a mutable or dirty checkout.

The vNext package is deliberately nested at `QA-Gen/qa_gen_pipeline/` and is
installed separately from BabyVision. Its `qa-gen` command must remain distinct
from the root project's `babyvision` command.

## Release boundary

Authoring and evaluation data cross the boundary in separate artifacts:

- The solver receives only the prompt, an anonymous scene reference, and a
  constrained viewer-session request. It must not receive a source asset ID or
  path, reference answer, seed, internal knowledge, credential, or audit data.
- The scorer receives the answer and rubric material needed to evaluate the
  solver output. Scorer artifacts are never solver-visible.
- Real asset resolution, evidence, reviews, receipts, and audit artifacts stay
  on the trusted host side. Anonymous scene references are resolved there, not
  in the solver bundle.

The package's leakage check is the release gate for solver-visible artifacts.
Mirroring source code and static fixtures does not publish any runtime bundle.

## Controlled viewer bridge

BabyVision keeps its existing Mol* and NiiVue pages as the renderer owners. The
vNext mirror adds the source-pinned `viewer-mcp-api.js` contract and the two
family-specific adapters as in-page, schema-validated bridges. They register
only after their viewer has loaded and fail closed when loading fails; they do
not create a network API or give a solver access to source files.

The bridge is capability evidence, not a release exception. A vNext medical
solver session still receives an anonymous scene reference only, and the
package's controlled viewer/release checks must keep masks and overlays outside
the solver-visible bundle.

`viewer_get_state` exposes only session-local structure identifiers, indexes,
visibility, and element counts. Source URLs, filenames, asset IDs, and source
formats remain trusted-host metadata and are covered by a source-pinned Node
regression test.

## Existing BabyVision owners

This integration does not modify or transfer ownership of:

- `QA-Gen/ScienceVisionTaskGenerator/` and the historical task-generation
  pipeline;
- existing `QA-Gen/tasks/` batches;
- `data/qagen_index.json` or viewer-owned indexes and pages;
- `harbor_adapters/`, `scripts/harbor/`, or existing Harbor task conversion and
  scoring flows.

The vNext package must not read or write the legacy QA indexes directly. A
future integration must use an explicit, reviewed release adapter: it consumes
an exported vNext solver/scorer release, validates its provenance and leakage
receipts, and then translates it into a separately versioned legacy task,
index, viewer, or Harbor format. That adapter is future work and must not be
hidden inside mirror synchronization.

## Runtime and private state

`.qagen/`, private registries, approval receipts, provider configuration,
release staging, and audit outputs are runtime state and must never be mirrored
or committed. Use an external workspace, or a repository-ignored path such as
`.local/qagen-workspaces/<run-id>`.

Do not use `QA-Gen/qa_gen_pipeline/` itself as `--workspace`. The checked-in
`knowledge/index.yaml` is a static fixture index. Runtime knowledge writes a
different schema to `<workspace>/knowledge/index.yaml`; combining the roots
would corrupt the fixture/runtime boundary.

## Concurrent mirror pull requests

PRs #112 and #114 each add independent entries to `.mirror-include`. When
either lands, rebase the remaining mirror PR and resolve the manifest conflict
as a union: preserve every existing line, both PRs' additions, and the vNext
package/document entries. Never choose one side wholesale or remove a path
merely to make the conflict disappear.

Do not use a full sync as the conflict-resolution mechanism. The sync planner
can delete files under mirrored prefixes when the selected ScienceVision
revision does not contain another PR's payload. After resolving the manifest,
run the boundary test and inspect a sync dry-run; unexplained deletions or
changes outside the intended vNext paths are a stop condition.
