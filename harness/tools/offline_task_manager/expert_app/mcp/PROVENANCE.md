# Parent-page browser-MCP runtime — provenance

These four files are the **parent page** half of the browser MCP layer: the
schema-driven Functions panel (`task-mcp-controls.*`) and the expert
Trajectory capture drawer (`task-trajectory.*`). They are vendored, not
authored here.

The **iframe** half (`viewer-mcp-api.js`, `screenshot-api.js`,
`molstar-mcp-adapter.js`, `niivue-mcp-adapter.js`) also ships inside each
task package's `viewer/shared/`. Most of it is duplicated here as well, from a
different source — see "Platform half" below. `screenshot-api.js` is the
exception: it exists only in the packages.

## Source

Vendored from the released offline expert review packages of 2026-08-14 —
the build that the 18 vendor reviewers are annotating with today:

```
scale-v2.3-physical-parent854-review540-expert-packages-20260814-v1.1.1/
  <domain>-<reviewer>-30-20260814-v1.1.1/viewer/shared/
```

All 36 copies of each file (18 reviewer packages x release v1.1.0 and
v1.1.1) are byte-identical, so the release has exactly one canonical
version of this runtime.

| file | sha256 | bytes |
| --- | --- | --- |
| `task-mcp-controls.js` | `03f2e1ef21c3abf04282d8f395625a320b2c30a7f1685dc1211ed3076d29af09` | 46389 |
| `task-mcp-controls.css` | `a686ce42616116950ce44d4a35ab731b2db4dfa3d34ac444521aacee8202a823` | 9386 |
| `task-trajectory.js` | `6e4065b0246f63009894a535cb4f782c4de291e99b9a02e4a450e0f7adcc848d` | 64500 |
| `task-trajectory.css` | `bc47f9829cff6bda64fdbf036070090dc8d250b3bca1e716074c8f95ee695577` | 5280 |

Two of the four files have since been changed here; see "Local changes"
below. The table above is the vendored baseline, not the current content.

These blobs exist in **no** git repository — they are build output that was
never committed upstream. Checking them in here is the first time this
runtime enters version control, which is appropriate: BabyVision-v2 is the
authoritative public core library.

## Platform half — registries and viewer adapters

Added 2026-09-12 from `origin/prod` @ `6c7e3794`. That is the deployed
platform tree; `prod` and `dev` are the same commit, so there is one surface
to track, not two. Eighteen files, none of which previously existed on this
branch:

- `registries/*.json` — the seven viewer registries (`molstar.v1`,
  `molstar.v2`, `niivue.v1`, `bim_coordination.v1`,
  `architecture_inspection.v1`, `gltf.v1`, `occt.v1`). 127 entries naming 59
  distinct tools. These registries are what an expert's Functions panel is
  built from, and they are the authority the parity table describes.
- the adapter set a registry entry is executed by
  (`architecture-inspection-`, `bim-coordination-`, `gltf-`, `molstar-`,
  `niivue-`, `occt-mcp-adapter.js` plus `molstar-chain-scope.js`), together
  with `viewer-mcp-api.js` and its trajectory pair (`trajectory-model.js`,
  `trajectory-sync.js`).
- `VIEWER-ADAPTER-WIRING.md`, which records how the adapters are wired to the
  viewer.

Why they belong here: `docs/mcp/PLATFORM_PARITY.md` maps *these* tool names
onto ccmcp, and a reader has to be able to check the entries it describes
against the registries themselves. Until now only the two derived documents
were here, and the registries lived solely on `prod`/`dev`.

These are copies of a deployed tree, not a second source of truth. An
unrecorded divergence from `origin/prod` is a bug.

### One file here is ours, not prod's

`render_library_doc.py` has no counterpart on `prod` — it is the generator for
`docs/mcp/MCP_LIBRARY.md`, and it lives beside the registries because it reads
them and nothing else. It is the one deliberate addition to this directory, and
the rule above does not apply to it. Everything else here is a straight copy.

## Why this version and not a newer one

Three other variants of `task-mcp-controls.js` exist on the build machine.
The 2026-08-14 release was chosen deliberately:

1. **It is what the offline reviewers already use.** Online experts and
   offline reviewers must see the same UI, or their annotations are not
   comparable. This version's panel is labelled "Record GT" and lays the
   capture sheet out as one continuous surface with the viewer kept
   full-size behind a floating overlay.
2. **It is self-consistent.** Every package carries a
   `.sciencevision-mcp-runtime.json` descriptor whose recorded hashes match
   the files on disk exactly (verified: 0 drift).
3. **The newer variant is unverified work in progress.** A 2026-08-17
   snapshot exists whose own descriptor no longer matches its
   `task-trajectory.js` on disk, and which additionally requires a new
   `mcp-tool-catalog.js` inside the iframe. That file is absent from the
   nine immutable task packages already deployed, so shipping its parent
   half would split the runtime across two incompatible generations.

## Local changes on top of the vendored baseline

`task-mcp-controls.js` and `task-mcp-controls.css` now carry a change that is
**not** in the released packages: the Functions panel reports what a click
actually did. The hashes below are the files as this branch ships them, so they
also include the language layer described under "Local modifications", which is
why `task-trajectory.*` is listed as well even though the feedback change does
not touch it. The two changes are independent and each has its own section.

| file | sha256 | bytes |
| --- | --- | --- |
| `task-mcp-controls.js` | `3858dc200c08bf273e873734aece13f7785bebaed60ec672e35e7dd48d46b876` | 55973 |
| `task-mcp-controls.css` | `2581c7a9ca86f84ed154724113d12c1b52d38dc6fd34d6788398b70570121af9` | 10305 |
| `task-trajectory.js` | `ad7c6d640d93034bba36d21d0df158b7499161e1410b0b7c3fcf0fd68f125041` | 74186 |
| `task-trajectory.css` | `bc47f9829cff6bda64fdbf036070090dc8d250b3bca1e716074c8f95ee695577` | 5280 |

Why: in the vendored build the only execute affordance is a button labelled
"Preview", and its only progress or outcome report is the `.sv-mcp-status`
span inside the panel's `<summary>` — which `.sv-mcp-controls:not([open])
.sv-mcp-status { display: none }` hides whenever the panel is collapsed. A
failed call also left its receipt inside a closed "Raw receipt" `<details>`.
The result is that pressing the button can look like it did nothing at all.

The change is presentational only. It adds no new command, alters no
argument, and does not touch the receipt contract or the trajectory payload:

- the button reads "Run preview", carries a title explaining reset -> replay
  -> execute, and switches to "Running..." while a call is in flight;
- a `.sv-mcp-feedback` line inside the command block reports start, each
  stage, and success or failure, so it stays visible when the panel header
  scrolls out of view;
- the receipt `<details>` opens by itself on any error.

Comparability consequence: this is a divergence from the UI the 18 offline
reviewers see, so it is a deliberate exception to the rule stated above. It
is presentational and it only adds information, so a step recorded through
the new panel is the same step the old panel would have recorded. Republishing
the offline packages with the same parent half would remove the divergence.

## The deployed iframe half is host-served, and its lineage is pinned

The live site does not serve the iframe half out of a task package. It serves
it from the host at `/sv-annotate/viewer/...`, from whatever tree
`build_shared_runtime(viewer_root=...)` was last pointed at, behind a
`runtime=vrt_...` cache token. Fetched through an authenticated session on
2026-08-19 and compared byte-for-byte against every candidate on the build
machine, the deployed tree is exactly:

```
review270-expert-packages-20260814-v1.0.0/<domain>-<reviewer>-30-.../viewer/
```

(diff 0 after undoing the host's `/viewer/` -> `/sv-annotate/viewer/` rewrite
and the runtime token; `molstar-mcp-adapter.js` = `86bfa9bb83e8...`.) It is
**not** the v1.1.0 or v1.1.1 tree this directory's parent half was vendored
from, and it matches no commit in either repository.

Two consequences:

1. Iframe-half defects **can** be corrected without republishing any package:
   patch the tree at `viewer_root` and POST `/api/admin/runtime/build`.
   `_copy_runtime` is idempotent on an already-injected page, so a patched
   tree rebuilds cleanly.
2. The two halves come from different package generations (iframe v1.0.0,
   parent v1.1.1). They agree on the tool vocabulary, which is what the
   command boundary requires, but the pairing is incidental rather than
   pinned by anything in the build.

## Compatibility with the deployed task packages

The parent page and the iframe must agree on the tool vocabulary. Verified
against the live package `expert_bio_001--scale-v2.2-expert-v2-r1`:

- `viewer-mcp-api.js` (`7a3e561915d5…`) and `screenshot-api.js`
  (`ed12369c3549…`) are byte-identical to the 2026-08-14 release, so the
  command boundary is unchanged.
- Tool **names** match exactly: 9 for Mol\*, 12 for NiiVue. The deployed
  `molstar-mcp-adapter.js` is a 2026-08-13 build (`86bfa9bb83e8…`) rather
  than the release build (`03a10ecd6947…`), but the only difference is
  inside `viewer_reset`.

### Known gaps in the deployed iframe half

Both are in the deployed 2026-08-13 Mol\* adapter (`86bfa9bb83e8...`), so
both affect the online experts and the 18 offline reviewers equally.

1. **`viewer_reset` blanks the viewport.** `createProvider` snapshots
   `camera.getSnapshot()` synchronously after `await loadStructureFromUrl`,
   before Mol\*'s asynchronous structure auto-fit has converged, and
   `viewer_reset` then restores that pre-fit camera. Every reset-and-replay —
   which is how both Preview and Save step begin — therefore frames empty
   space. Measured on the live site with request interception (non-white
   pixels in a 160x100 downscale of the canvas, out of 16000):

   | | baseline | after `viewer_reset` | after `viewer_rotate` |
   | --- | --- | --- | --- |
   | deployed | 1253 | 5 | 7 |
   | with the settle fix | 1253 | 1253 | 1267 |

   The fix is `settleInitialCamera` in ScienceVision commit `ae1198d7`, which
   waits for the camera signature to stop changing before the provider
   records its reset baseline.

2. **Stale measurement labels.** The deployed adapter does not clear Mol\*
   measurement annotations on `viewer_reset`; the 2026-08-14 build does. A
   `viewer_measure_distance` annotation therefore survives a reset, so
   reset-and-replay can accumulate stale distance labels.

Contrary to what this file said before, neither requires republishing the
packages for the **online** site: see "The deployed iframe half is
host-served" above. The offline reviewer packages are immutable, so for those
18 reviewers a republish is the only route.

## Local modifications

The hash table above records what was vendored, not a freeze. The workspace
that loads these files is Chinese-first with an English toggle, and the
upstream build has no language layer at all, so the copy is routed through
the shared one in place:

- `task-mcp-controls.js` — every user-facing string resolves through a local
  `t(key, fallback)` helper that delegates to `window.SVI18n` and returns the
  original English when no layer is present. Idle copy moved into
  `applyIdleText`, and an `SVI18n.onChange` subscriber redraws the panel on a
  switch. Keys live under `mcp.*` in `online_app/i18n.js`. The execution
  feedback added by the change above is routed the same way (`mcp.feedback.*`,
  plus `mcp.panel.run` / `running` / `runHint`): the button label is resolved by
  `setRunBusy` rather than stored in a `data-` attribute, so a language switch
  mid-call redraws "Running..." instead of relabelling a button that is still
  waiting, and `applyLanguage` asks `state.running` which wording to draw. The
  feedback line follows `task-trajectory.js` below and remembers the key it was
  last called with, so a switch after a run redraws that outcome instead of
  leaving it in the language it happened to be drawn in.
- `task-trajectory.js` — same helper and the same fallback rule. Status copy is
  remembered as a recipe (`setStatusText`) rather than as finished text, so a
  switch redraws whatever the last operation reported instead of blanking it;
  `applyStaticText` rewrites the chrome that `buildUI` only builds once. Keys
  live under `trajectory.*`.

Thrown `Error` messages keep their English wording, and are translated where
they are drawn. The wording is what a receipt records and what lands in logs and
test output, so changing it would change the record; but it is also what the
status line shows the expert, and there it has to be readable. The catalog keys
the Chinese wording by the English text (`error.*` in `online_app/i18n.js`,
looked up through `SVI18n.translateThrown`), a local `diagnostic()` helper
translates on the way to the element, and `setStatus` remembers the original so
a language switch redraws it in the language then chosen. Raw backend wording
takes the same route. The receipt fields do not: they store the English.

One behavioural change was made on top of the localization, at the reviewers'
request that the reasoning behind an operation be recorded: `task-trajectory.js`
now refuses to submit a trajectory whose saved steps do not all say why they
mattered. Saving a step in this drawer is already a deliberate act — the expert
replays, executes and captures on purpose — so it is the semantic equivalent of
a step recorded by hand on the form rail, and owes a reason. The gate fires at
submit, beside the existing final-result check: it never blocks a capture, and
it names the first step that still lacks a reason and focuses that field
(`trajectory.status.needNote`, `unexplainedStep`).

`trajectories.py` stays permissive on purpose. The client already deployed to
the platform posts steps with no description, so validating per step on the
server would reject work that is currently in progress; the obligation is
enforced only by the client this branch ships.

The tool vocabulary is the viewer's, not ours: it declares its function names,
argument names, dropdown choices and a sentence for each, and the panel receives
all of it in English over the iframe boundary. The wire names and the values sent
are untouched -- a task names its Ground Truth actions by them and every receipt
records them -- and what is read is glossed from a catalog keyed by that same
text (`viewer.*` in `online_app/i18n.js`, through `SVI18n.translateViewer`). A
function name keeps the wire name in the dropdown with the gloss beside it; an
argument label shows the gloss and keeps the name in its tooltip. A viewer this
workspace has no wording for reads exactly as it declared itself.

Layout is untouched, and a package that ships no `i18n.js` renders exactly the
wording in the vendored release.

`tests/offline_task_manager/test_i18n_layer.py` pins this routing, so
re-vendoring a newer upstream build fails the suite until the localization is
re-applied on top of it. That is deliberate: the alternative is a silent
return to an English-only panel.
