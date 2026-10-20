# Which MCP tools are actually in use

Four different sets get called "the MCP tools", and conflating any two of them
produces a wrong statement about what an eval measured. This document separates
them and gives the current count for each.

| # | set | count | authority |
|---|---|---:|---|
| 1 | **advertised** by `sciencevision-mcp` to a `ccmcp` task | **70** | `sciencevision-mcp --list-tools` |
| 2 | **allowed** for the question's viewer family, once gated | **11–19** live-session | `sci3d_agent/mcp/parity/allowlist.py` (ScienceVision) |
| 3 | **called** by an agent in the last real runs | **7** distinct (EV2) / **4** (scale-v3.4) | run trajectories |
| 4 | **declared** by the question's `tool_policy.allowed_tools` | 11 distinct names, **7 of which exist nowhere** | task JSONL |

Set 4 is the one that surprises people, so it is stated first.

> **Scope note for this repository.** The counts above are measured against
> ccmcp as it exists in `UniPat-AI/ScienceVision` @ `73813502`. This repository's
> own `sci3d_agent/mcp/tools` advertises **42** tools on both `main` and `prod`
> and lacks the whole measurement and selection surface -- see the "behind the
> ccmcp column" section of [`PLATFORM_PARITY.md`](PLATFORM_PARITY.md). Set 2 (the
> per-family gate) and the parity module live in ScienceVision; only their output
> is vendored here. Sets 3 and 4 are properties of the task corpora and the
> harbor adapters, which both repositories share.

## 4. What the questions declare does not name real tools

`tool_policy.allowed_tools` travels with every task into
`build_harbor_tasks` (`:207`) and is what the verifier's tool-policy check reads.
Measured across the two task sets that have run:

| task set | n | declared `allowed_tools` |
|---|---:|---|
| `scale-v1-sample50` | 150 | `viewer_rotate` (150), `viewer_zoom` (100), `viewer_measure_distance` (66), `viewer_slice` (50), `viewer_window_level` (35), `viewer_show_cell` (19), `viewer_bbox` (15), `viewer_switch_structure` (10) |
| `EV2_Pilot` | 15 | `sv3d_open_scene` (15), `sv3d_render` (15), `sv3d_reslice` (15) |

Checked against both layers — this repository's 70 advertised tools and the
platform's 59 (prod, 7 registries):

- **scale-v1's eight names intersect ccmcp in zero places.** Only
  `viewer_window_level` and `viewer_zoom` exist on the platform; the other six
  (`viewer_rotate`, `viewer_slice`, `viewer_bbox`, `viewer_measure_distance`,
  `viewer_switch_structure`, `viewer_show_cell`) exist on **neither** layer.
- **EV2's `sv3d_reslice` exists on neither layer.** The other two are real.

So 7 of the 11 distinct declared names name nothing that can be called anywhere.

### The consequence is a scored number, not a cosmetic one

`_verify_translate.py` exempts `Bash`/`Read` wholesale (`_GENERIC_HARNESS_TOOLS`)
and then treats any sci3d call outside a non-empty allow-list as out of policy.
With an allow-list made of `viewer_*` names:

- a `cc` trial calls only `Bash` → exempt → `process_tool_policy = 1.00`
- a `ccmcp` trial calls `sv3d_*` → outside the `viewer_*` list → **0**

That is exactly the shape of the 2026-08-13 leaderboard, where `cc` and all four
`cc2d` routes score `tool_policy` 1.00 and `ccmcp` scores 0.35. **The route that
honestly names its tools is the one penalised**, and the mechanism is a namespace
mismatch rather than any agent behaviour.

Fixing this needs a translation from the declared namespace to ccmcp names, and
that translation is what `docs/mcp/PLATFORM_PARITY.md` already is — the table maps
platform/`viewer_*` names onto the ccmcp tools that carry their meaning. Wiring
`tool_policy` through it is the next change; it is not done here.

## 1. Advertised: 70 tools, to every task

`scripts/harbor/templates/claude_code_mcp.mcp.json` names the server and no
arguments, so a `ccmcp` task gets the whole surface regardless of what its
question is about.

| category | n | what |
|---|---:|---|
| A catalog | 11 | dataset/scene/asset lookup |
| B URL builders | 4 | `sv_build_*_url` viewer links |
| C inspectors | 8 | read an asset off disk (`sv_inspect_*`) |
| D static renderers | 9 | offscreen PNG (`sv_render_*`) |
| E pipeline | 8 | build-time drivers, 5 read + 3 guarded write |
| **F live session** | **30** | `sv3d_*` — sessions, camera, render state, measurement, segmentation |

Only F is a viewer capability in the sense the platform means. The count moves
between revisions (58 in the audited checkout, 68 on an earlier mainline, 70
here), so a run must record it rather than cite a constant.

## 2. Allowed once gated to the question's viewer family

`sciencevision-mcp --viewer-family <registry>` narrows F to what a question
authored on that family could have been solved with, derived from the parity
table. Non-F categories are deliberately untouched — see
`allowlist.advertised_tools_for_family` for why withholding an inspector would
remove no capability and cost the trajectory its evidence.

| viewer family | F tools allowed (of 30) |
|---|---:|
| `niivue.v1` | 19 |
| `molstar.v2` | 18 |
| `occt.v1` | 17 |
| `architecture_inspection.v1` | 13 |
| `gltf.v1` | 12 |
| `molstar.v1` | 11 |
| `bim_coordination.v1` | 11 |

Five of those are the harness floor on every family — `sv3d_open_scene`,
`sv3d_close_scene`, `sv3d_get_state`, `sv3d_render`,
`sv3d_render_canonical_views` — which are the agent's substitute for having a
screen rather than capabilities the expert lacked.

For scale, the expert who authored a `molstar.v1` question had **9** viewer tools.
Ungated, the agent answering it is offered 70.

## 3. Called: a handful, and half of them errored

**EV2 pilot** (15 `ccmcp` trajectories, 24 MCP calls, 7 distinct tools):

| tool | calls |
|---|---:|
| `sv_inspect_molecule` | 13 |
| `sv_inspect_nifti` | 4 |
| `sv_get_asset` | 2 |
| `sv_render_nifti_mip` | 2 |
| `sv3d_open_scene` | 1 |
| `sv3d_segment_3d_volume` | 1 |
| `sv_render_nifti_slice` | 1 |

Note the shape: 22 of 24 calls are categories A/C/D — catalog, inspector,
offscreen render. The live-session surface, which is the whole point of the
`ccmcp` route, was touched twice.

**scale-v3.4 clean set** (17 `ccmcp` trials, 88 MCP calls of 162 total, **43
errored**):

| tool | trials | outcome |
|---|---:|---|
| `sv3d_open_scene` | 17/17 | succeeded — over a scene holding **zero atoms** |
| `sv3d_measure_distance` | 17/17 | 14/17 returned `no parsed atoms` first |
| `sv_inspect_molecule` | 10/17 | `missing required arg: path`; also `type must be one of: mmcif, pdb, sdf` (7) and `dependency missing: gemmi` (4) |
| `sv3d_select` | 7/17 | `selection must be a non-empty string` |

`gemmi` is absent from `sci3d-claude-code-mcp:v32bcm522-1e5b7359`, so
`sv3d_open_scene` returned a **successful** envelope over an empty ±10 Å
placeholder box on every trial, and every geometric query afterwards was answered
from that placeholder. All 17 trials ended up measuring coordinates the agent had
grepped out of the `.cif` itself. So on molecular assets from that image, a
`ccmcp` number measures `Bash`, not MCP 3D grounding.

Three trials also called names that exist in no registry —
`sv3d_inspect_molecule` (2) and `sv3d_inspect_molecule_placeholder` (1). The
trajectory audit records `steps[].tool_calls[].function_name` with no registry
check, so a guessed name lands in the audit beside the real ones. Any claim of
the form "the agent used tool X" has to be checked against `--list-tools` first.

## Reading order for someone citing a number

1. Which set is being quoted? A leaderboard row is produced under set 1 or 2, not
   3 or 4.
2. Was the run gated? An ungated row is not comparable with a gated one, and the
   only durable evidence is the run's own record of the surface it offered.
3. Did the image parse the asset? A molecular `ccmcp` row from an image without
   `gemmi` measures the shell.
4. Does the question's `tool_policy` namespace match anything callable? If not,
   `process_tool_policy` is measuring the namespace, not the agent.
