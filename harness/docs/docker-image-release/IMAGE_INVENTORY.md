# ScienceVision eval image inventory (H20 + Docker Hub)

Verified 2026-09-26: the H20 local image list and repo digests were cross-checked
against the Docker Hub tags API for the `suermars` namespace. Every digest below
matched on both sides. Re-run the commands in "Re-verification" before quoting
this page in a new record. A second pass later that day re-listed every H20
image (all IDs and digests below unchanged) and added the `jingelorian/jing`
CLI cross-check images in §B2; §B3 records the CLI fix pushed to Hub later
the same day.

Two image lines exist. They are built from different trees and their MCP tool
surfaces differ — read "Two lines, one surface rule" before mixing them.

## Which image runs which arm

| Arm (harness label) | Image | Route |
| --- | --- | --- |
| `cc` | `sci3d-codex:server-20260922-h20` (local `sci3d-codex:latest`) | Codex CLI, raw 3D file on disk, no MCP |
| `ccmcp` (v9/250 line) | `sci3d-codex-mcp:server-20260922-h20` (local `sci3d-codex-mcp:latest`) | Codex CLI; `sciencevision-mcp` bundled **inside the agent image**, registered as a stdio MCP server |
| `ccmcp` (release harness) | wrapper `suermars/sci3d-codex-mcp:server-20260923-h20` + worker `suermars/sci3d-mcp-worker:<release tag>` | wrapper is a thin agent-only image; the MCP server runs in the separate private worker over HTTP (`127.0.0.1:8765/mcp`) |
| `cc2d-1/3/6/12` | `sci3d-codex-2d:server-20260922-h20` | Codex CLI, pre-rendered view PNGs only |
| claude-code family | `sci3d-claude-code{,-mcp,-2d}:server-20260922-h20` | same three routes on Claude Code |
| scoring | `suermars/sci3d-scorer:server-20260928b-h20` (current); `server-20260928-h20` and `server-20260923-h20` (earlier, published untouched); `sci3d-scorer:harbor-0.20-v1` = local alias of the 0922 scorer | deterministic scorer, network disabled |

## A. Release line (`eval-baseline`)

Built from `/Users/sue/Research/ScienceVision/eval-baseline-release`
(branch `eval-baseline`), staged and built on H20. Source revisions below are
the `org.opencontainers.image.revision` labels (synthetic staging commits).

| Role | Tag | Image ID (H20) | Hub digest | Built |
| --- | --- | --- | --- | --- |
| MCP worker (this round) | `suermars/sci3d-mcp-worker:server-20260924-h20` | `faf3ab910f17` | `sha256:08eca2ae6a9401d1801577e2573fae1f243d1ebcda8eb804cb3dec9b71388819` | 2026-09-25 10:14 CST; label `e80fdd314ac5f4ed894d6281d1bd1e33f83003c4` |
| MCP worker (previous) | `suermars/sci3d-mcp-worker:server-20260923-h20` | `7c3295fe91f3` | `sha256:4a8e593f4f89b2a89be104902ca7c656dca321a3acc434b7bc07ed47b8c7c60c` | label `3a76528993a0ba04ff4593631c92d36527bb93a5` |
| Codex agent-only wrapper | `suermars/sci3d-codex-mcp:server-20260923-h20` | `292b5fcab207` | `sha256:8b8dafb4c7bea19b30325f53cd641241b9d56f535092d394f3d10a6ae7dc9d96` | label `3a765289…` |
| Claude Code agent-only wrapper | `suermars/sci3d-claude-code-mcp:server-20260923-h20` | `ae1e00c73c00` | `sha256:d11ddea7b06e08e020832de5a9a7cfcafffaa6fb0f47edb3ff46fb4dc7c347e8` | label `3a765289…` |
| Deterministic scorer (current) | `suermars/sci3d-scorer:server-20260928b-h20` | `6e8879605f05` | `sha256:7e5471e1149ad8d84f15b4eab98d8d34d0070ee5fec15b470d2f001be6b9aea1` | 2026-09-28 13:49 CST; context from `eval-baseline-release` @ `d41b21bc`, manifest `d0c3ee1b…`; adds the list comparators and the null round trip |
| Deterministic scorer (previous, same day) | `suermars/sci3d-scorer:server-20260928-h20` | `a360130456e5` | `sha256:e90dfa22220b82d3807cc6029bbc4c9be7365debed3ff817dffd0b6352d8d801` | 2026-09-28 13:17 CST; context @ `0b5cf723`, manifest `c2da0e88…` |
| Deterministic scorer (previous) | `suermars/sci3d-scorer:server-20260923-h20` | `5b055ff9d995` | `sha256:3730ff689fb58afd8e5576d9f59e0429c6b8e341fc3652212659b9c76accee02` | label `e99f428b39135a1ca293c7cc486dd8cfe8596961` |

The 20260924 worker tag was published to Docker Hub on 2026-09-25 (Hub
`last_updated` 2026-09-25T03:32:16Z; H20 repo digest equals the Hub digest), after
the release record was first written — the manifest's push status is corrected
in `RELEASE_MANIFEST-20260924.json`.

## B. Run line (v9/250 agent images)

Built on H20 from the `Science-Vision` checkout (`/home/matianyi/Science-Vision`)
via its `scripts/harbor/templates/build_*_image.sh` scripts. These images carry
**no** revision label; provenance for them comes from this page and the build
logs. Their `latest` local tags are what the v9/250 task trees referenced.
The three claude-code tags ship Claude Code **2.1.197**, below the 2.1.280
model floor — see §B2 and §B3.

| Role | Tag (local alias) | Image ID (H20) | Hub digest |
| --- | --- | --- | --- |
| Codex, raw file | `suermars/sci3d-codex:server-20260922-h20` (`sci3d-codex:latest`) | `3248db009ea9` | `sha256:b3790ae10ecbbe85ab28fca46424f1926eca5bab51744e3d343073c7d23913b6` |
| Codex + MCP | `suermars/sci3d-codex-mcp:server-20260922-h20` (`sci3d-codex-mcp:latest`) | `89b16eebfdd3` | `sha256:a8ff9a869036efb271fea42930cdb3f257f48b58f0c4a7c0422a24a180871519` |
| Codex 2D | `suermars/sci3d-codex-2d:server-20260922-h20` (`sci3d-codex-2d:latest`) | `3a61dd649653` | `sha256:bf32763651ceb085c435708ad1354fbcbbaf37d8863f141030b9c779e80c3e82` |
| Claude Code, raw file | `suermars/sci3d-claude-code:server-20260922-h20` (`sci3d-claude-code:latest`) | `294f666cd2f8` | `sha256:f7abff0b0cefdb59df1da621e97a2ee8c0d7df72859d64b1376419a4fbfe2fce` |
| Claude Code + MCP | `suermars/sci3d-claude-code-mcp:server-20260922-h20` (`sci3d-claude-code-mcp:latest`) | `d18f0b8642c1` | `sha256:562fca8bd621c299e4f68aa1ccc103c913755310c5c8a1eff9348783d9f00d28` |
| Claude Code 2D | `suermars/sci3d-claude-code-2d:server-20260922-h20` (`sci3d-claude-code-2d:latest`) | `c0f3bea67d35` | `sha256:735bd1a9665a830cf4122555c18c80a245cf99a336df75112360ca7a39eaa6ac` |
| Scorer (previous round) | `suermars/sci3d-scorer:server-20260922-h20` | `2ad8d32aee4e` | `sha256:7c85a22b3e9dea7e140939f3ab6a3db1f2bfa7e8fec8964b87f90c86735b3004` |

Hub URLs: `https://hub.docker.com/r/suermars/<repo>` (tag list:
`.../r/suermars/<repo>/tags`).

## B2. Claude Code CLI cross-check images (`jingelorian/jing`, pulled 2026-09-26)

Every claude-code image above ships Claude Code **2.1.197**; newer models
require ≥ 2.1.280, and the rejection surfaces as a well-formed zero run rather
than an error (no errored-trial flag). Jing pushed CLI-fixed copies — one
added `RUN npm install -g @anthropic-ai/claude-code@latest` layer each — and
H20 pulled them on 2026-09-26. Verified: exactly one added layer, base layers
preserved as a prefix, `claude --version` as below.

| Image | CLI in image | Layers | ID (H20) | Hub digest |
| --- | --- | --- | --- | --- |
| `jingelorian/jing:sci3d-claude-code-cli2.1.282` | 2.1.282 | base+1 (base = 20260922) | `d99c7d906f3a` | `sha256:f0fb3bdb875e…` |
| `jingelorian/jing:sci3d-claude-code-2d-cli2.1.282` | 2.1.282 | base+1 (base = 20260922) | `2fb470660330` | `sha256:f48f9cd05e17…` |
| `jingelorian/jing:sci3d-claude-code-mcp-cli2.1.282` | 2.1.283 | base+1 (base = **20260923 wrapper**) | `8359230d29aa` | `sha256:aeecedd51ecf…` |

The mcp copy sits on the worker-flow wrapper, not the bundled run-line image —
it is a worker-flow agent candidate, not a drop-in for the v9 bundled trees.
At cross-check time no alias had been re-pointed; §B3 records the 2026-09-26
fix that re-points the run-line `:latest` aliases to the fixed images.

## B3. 2026-09-26 Claude Code CLI fix round (`server-20260926-h20`)

Every claude-code image shipped CLI 2.1.197, below the 2.1.280 floor newer
models require (§B2). Rebuilding from scratch proved unsafe on 2026-09-26: a
plain rerun cache-hits the npm layer and reproduces the 2.1.197 images
unchanged (the post-build version gate caught this), and a forced rebuild of
the bundled mcp image fails today — the frozen pip line
(`"mcp>=1.0" "fastmcp>=0.5"`, unpinned, tree `567b737f`) no longer resolves
`httpx`, which `_segment_client` imports (`ModuleNotFoundError`; add `httpx`
or pin before the next full rebuild). The fix therefore ships as one added
layer on the existing run-proven images — the same structure as the
jingelorian copies — pinned to 2.1.283 and installed from
`registry.npmmirror.com` (container-side npmjs tarball downloads stall from
this host; the mirror finishes in seconds):

```dockerfile
FROM <existing image>
RUN npm install -g @anthropic-ai/claude-code@2.1.283 --registry=https://registry.npmmirror.com && claude --version
```

| Role | Tag (local alias) | Image ID (H20) | Hub digest | CLI |
| --- | --- | --- | --- | --- |
| Claude Code, raw file | `suermars/sci3d-claude-code:server-20260926-h20` (`sci3d-claude-code:latest`) | `8eb9bd119993` | `sha256:f273f9ee34cd…` | 2.1.283 |
| Claude Code 2D | `suermars/sci3d-claude-code-2d:server-20260926-h20` (`sci3d-claude-code-2d:latest`) | `5bcf4ab505a2` | `sha256:8d8aa572ace2…` | 2.1.283 |
| Claude Code + MCP (bundled) | `suermars/sci3d-claude-code-mcp:server-20260926-h20` (`sci3d-claude-code-mcp:latest`) | `b45332be6031` | `sha256:f8043c7a2805…` | 2.1.283 |

Verified before push: `claude --version` = 2.1.283 in all three; the mcp tool
surface is identical name-for-name to the previous image; layer structure is
base+1 with the base layers preserved as a prefix. Rollback IDs:
`294f666cd2f8` / `c0f3bea67d35` / `d18f0b8642c1`. The 0923 worker-flow
wrapper (`suermars/sci3d-claude-code-mcp:server-20260923-h20`) still sits on
the 2.1.197 base; it is left for the next release round, as is pinning the
CLI in the templates.

Push note: the cc manifest PUT timed out once (`TLS handshake timeout`, flaky
link) and was re-pushed; the Hub digests above were re-read from the tags API
after the retry.

## C. Local-only images (H20, not on Docker Hub)

| Image | ID | Note |
| --- | --- | --- |
| `sci3d-scorer:harbor-0.20-v1-mcpfix` | `2ad8d32aee4e` | alias of the 20260922 scorer above |
| `sci3d-scorer:harbor-0.20-v1` | `627c920be007` | pre-mcpfix scorer |
| `pbx-claude-bridge:multi-slim` | `fdb832c67409` | chat-completions bridge for the claude-code cohort (PaperBenchX image) |

## D. H20 paths

```text
# release line — staging and build inputs
/home/matianyi/sv-eval-baseline-release-20260924-wq3f7k/   # this round (label e80fdd31)
/home/matianyi/sv-eval-baseline-release-20260923-tmwnSV/   # previous round; keeps scorer-context-authoritative/
/home/matianyi/build-worker-20260924.sh                    # worker build script (this round)
/home/matianyi/logs/build-worker-20260924-*.log            # build logs (incl. the -v2 rerun)
/home/matianyi/sci3d-worker-smoke.sh, ~/sci3d-worker-smoke.py

# run line — checkout the agent images were built from
/home/matianyi/Science-Vision/                             # worktree carries the platform-vocabulary tools
  └─ scripts/harbor/templates/build_{codex,codex_mcp,codex_2d,claude_code*}_image.sh
/home/matianyi/logs/arch26-build146*.log, ~/logs/build-scorer-mcpfix-20260922T095801Z.log

# v9 run roots
/nvme4/matianyi/ScienceVision-evals/v9-250-codex-4arm-20260923/
/nvme4/matianyi/ScienceVision-evals/v9-250-4arm-20260923/      # claude-code cohort
/nvme4/matianyi/ScienceVision-evals/v9-reground30-20260925/    # in flight
```

## E. Dockerfile / build-script inventory

Both copies carry the same layout: `Dockerfile.<role>` plus a
`build_<role>_image.sh` wrapper.

```text
mac: /Users/sue/Research/ScienceVision/eval-baseline-release/scripts/harbor/templates/
H20: /home/matianyi/sv-eval-baseline-release-2026092{3,4}-*/scripts/harbor/templates/

Dockerfile.sciencevision-mcp-worker  + build_sciencevision_mcp_worker_image.sh  # private MCP worker
Dockerfile.codex / .codex-mcp / .codex-2d      + build_codex*_image.sh          # codex family
Dockerfile.claude-code / -mcp / -2d            + build_claude_code*_image.sh    # claude-code family
```

## F. Two lines, one surface rule

The ccmcp arm is only comparable across runs if its tool surface mirrors the
**expert platform's** registries (the release repo carries a readable copy at
`tools/offline_task_manager/expert_app/mcp/registries/`: `molstar.v1/v2`,
`gltf.v1`, `niivue.v1`, `occt.v1`, `bim_coordination.v1`,
`architecture_inspection.v1`). That mirroring is the design intent, and the
run-line image implements it.

| | Run line `sci3d-codex-mcp:server-20260922-h20` | Release-line worker (20260923 **and** 20260924) |
| --- | --- | --- |
| Tool count | **73** | **58** |
| Platform-vocabulary tools | yes: `sv3d_select`, `sv3d_isolate`, `sv3d_measure_{distance,angle,dihedral,extent}`, `sv3d_list_components`, `sv3d_clash`, `sv3d_get_unit_cell`, `sv3d_set_representation`, `sv3d_set_color_scheme`, `sv3d_set_oblique_plane`, `sv3d_pick_entity`, `sv3d_clear_selection` | none of them |
| Molecule files | `sv3d_open_scene` opens them | `_detect_kind` rejects every molecule suffix |

That the run-line surface is deliberately platform-defined is visible in its own
`--list-tools` output, which is a surface descriptor rather than a bare list:

```json
{"surface": {"schema_version": "sciencevision.mcp_surface.v1", "id": "ccmcp@3ccee5b7",
 "platform": {"repo": "UniPat-AI/BabyVision-v2", "ref": "origin/prod",
              "commit": "3ccee5b7096f5be56b963ee83882f9c3bd624adc",
              "committed": "2026-09-09T11:50:12+08:00"},
 "platform_names": {"total": 59, "by_status": {"same_name": 10, "renamed": 6,
                    "split": 1, "folded": 3, "partial": 15, "absent": 24}},
 "tools": 73, "ccmcp_tools": {"named_by_parity_table": 25, "only_in_ccmcp": 48}}}
```

So "the MCP comes from the expert platform" is the intended setting: the ccmcp
surface is declared against the BabyVision-v2 platform revision and tracked
name-by-name. The release-line worker predates that parity layer.

Consequence: a worker rebuilt from the current release snapshot ships a
**smaller, differently-shaped** ccmcp surface than the runs Jing reviewed. Before
the next scored ccmcp run, the release line's `sci3d_agent` must be refreshed
from the revision that carries the platform-vocabulary tools (or the release
harness must be pointed at the run-line image). This is open — see the session
record for the decision.

## G. Re-verification

```bash
# H20: local tags, IDs, repo digests
ssh UniPat-ScienceVision-H20 "docker images --digests | grep -E 'sci3d|suermars'"

# H20: an image's tool surface
ssh UniPat-ScienceVision-H20 \
  "docker run --rm --entrypoint sciencevision-mcp <image>:<tag> --list-tools | jq '.tools | length'"

# Docker Hub: tags + digests (public, no auth)
curl -s https://hub.docker.com/v2/repositories/suermars/<repo>/tags \
  | jq -r '.results[] | "\(.name) \(.digest) \(.last_updated)"'
```
