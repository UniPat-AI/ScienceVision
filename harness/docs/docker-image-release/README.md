# ScienceVision Docker image releases

This directory is the audit record and operating guide for the split
agent/MCP-worker/scorer image release process. It documents the 2026-09-23
H20 publication without storing Docker Hub credentials, auth files, tokens,
or private registry configuration.

## 2026-09-23 release

All published images use the immutable release tag
server-20260923-h20. The latest tag was deliberately not created, moved, or
overwritten.

| Role | Docker Hub image | Hub digest | H20 local image ID |
| --- | --- | --- | --- |
| Private MCP worker | suermars/sci3d-mcp-worker:server-20260923-h20 | sha256:4a8e593f4f89b2a89be104902ca7c656dca321a3acc434b7bc07ed47b8c7c60c | sha256:7c3295fe91f30cb1ffad9cd16f32a49c02a1c85a905baeff1909203402eee5ab |
| Codex agent-only wrapper | suermars/sci3d-codex-mcp:server-20260923-h20 | sha256:8b8dafb4c7bea19b30325f53cd641241b9d56f535092d394f3d10a6ae7dc9d96 | sha256:292b5fcab2078e21cb0be3d62c077492051ab79f067a5498ac09e11df844c64e |
| Claude Code agent-only wrapper | suermars/sci3d-claude-code-mcp:server-20260923-h20 | sha256:d11ddea7b06e08e020832de5a9a7cfcafffaa6fb0f47edb3ff46fb4dc7c347e8 | sha256:ae1e00c73c00bc4f77cf2707b87245efdf7ed9ecaf10f7b8d3178b6799aa3301 |
| Deterministic scorer | suermars/sci3d-scorer:server-20260923-h20 | sha256:3730ff689fb58afd8e5576d9f59e0429c6b8e341fc3652212659b9c76accee02 | sha256:5b055ff9d995746a945534786c211dbe4a39a9260e4244cfc660d3f8ac421c1a |

The scorer's embedded manifest label is
8f15d35d13b683d39d5dc3bada99d3f808d9103a48c96917f56eaed31790113d.

The writable release source was
/Users/sue/Research/ScienceVision/eval-baseline-release. The H20 audit
directory remains at
/home/matianyi/sv-eval-baseline-release-20260923-tmwnSV and retains the
staged input archive and authoritative scorer context.

## What was verified

- The targeted packaging, task-generation, runtime, and scorer test suite:
  57 passing tests locally and on H20 using Python 3.12.
- The worker runs as UID 10001, can read its private asset registry and
  matched-QA index, and successfully handles sv_get_asset and
  sv_search_matched_qa.
- Codex and Claude Code wrappers contain neither evaluator/MCP source nor
  viewer/ or datasets/ content.
- A real HTTP MCP initialize request from each wrapper to a healthy worker
  in a shared network namespace returned HTTP 200.
- The scorer builds and runs with Docker build/runtime networking disabled,
  as its image contract requires.

Read [SOP.md](SOP.md) before a new release. [FILE_LAYOUT.md](FILE_LAYOUT.md)
defines the permitted image filesystem boundaries;
[ASSET_VISIBILITY.md](ASSET_VISIBILITY.md) defines task-asset and annotation
isolation; [SCORER_RELEASE.md](SCORER_RELEASE.md) defines the scorer-specific
contract; [RELEASE_MANIFEST.json](RELEASE_MANIFEST.json) is the
machine-readable release record; and [IMAGE_INVENTORY.md](IMAGE_INVENTORY.md)
lists every current image with its H20 build path, Docker Hub URL, and digest.

## 2026-09-24 release round (worker rebuild)

The 3D/MCP corrections (view protocol, near planes, molecule frame + HETATM,
STEP routing, exclusion receipts) shipped as a rebuild of the **private MCP
worker** only: `suermars/sci3d-mcp-worker:server-20260924-h20` was built and
smoke-verified on H20 from staging commit `e80fdd31` (image id
`sha256:faf3ab91…08ed396`) and **published to Docker Hub on 2026-09-25**
(digest `sha256:08eca2ae…`, re-verified 2026-09-26). The wrappers and
the scorer are unchanged this round and are reused at their 20260923 digests —
see [RELEASE-20260924.md](RELEASE-20260924.md) and the machine-readable
[RELEASE_MANIFEST-20260924.json](RELEASE_MANIFEST-20260924.json) for the full
record (source revisions, archive hash, verification, rollback).

Before the next scored run: **regenerate** the cc2d trees (not patch them) so
`render_manifest.json` carries `scheme: "cc2d-views/2"` and publish the tree
audit together with its `cc2d_exclusions.jsonl` receipt. The STEP hook needs
the OCP CAD kernel on the tree-generation host (H20).

One open decision gates any ccmcp comparison: the release-line worker ships 58
MCP tools while the v9/250 runs used a 73-tool surface carrying the platform
vocabulary (`sv3d_select`, `sv3d_measure_*`, …). See
[IMAGE_INVENTORY.md](IMAGE_INVENTORY.md) §"Two lines, one surface rule".

### Mirror parity

Every path this change touches (`sci3d_agent/**`, `scripts/harbor/*.py`,
`tests/harbor_adapters/**`, `tests/mcp/**`) is a `.mirror-include` path. Running
`tools/sync_from_sciencevision.py` against the current ScienceVision checkout
would revert these files and DELETE the 0923-only worker files upstream does not
have. The reciprocal port into the ScienceVision branch (or an updated
`.mirror-include`) is REQUIRED before any sync run.

## 2026-09-28 scorer round (membership, closed-loop band, list comparators, null TOML round trip)

The scorer was rebuilt and published twice the same day. The current tag is
`suermars/sci3d-scorer:server-20260928b-h20` (Hub digest
`sha256:7e5471e1149ad8d84f15b4eab98d8d34d0070ee5fec15b470d2f001be6b9aea1`, H20
local image ID
`sha256:6e8879605f05d1bd8c90f2ed4219830552f0ec849c6272395d3176ef92b04b05`);
the earlier `server-20260928-h20` stays published untouched. Four changes ride
this round:

- the deterministic scorer gains `voxel_member_of_set`, a pure-stdlib
  comparator judging a voxel answer against a bit-packed ground-truth region
  shipped inside the rubric item (`scripts/harbor/_membership_rules.py` packs
  it at build time; records opt in via `radiologist_mask_membership` or a
  `mask_path` + `mask_id` pair). v9's `med_lesion_point` tasks keep the
  ±1-voxel band for now — their native CT grids are not the masked corpus;
- the derived measurement band switches to the closed-loop agreement of
  2026-09-23: exact match with the third decimal place free, `±0.005` for every
  float measurement (angles keep `periodic_modulus=360`). This is a
  tree-build-time rule (`scripts/harbor/_tolerance_rules.py`), so only trees
  built after this round carry it;
- label/object lists are gradable at last: `_default_comparator` used to send
  every list to `vector_abs_tolerance`, so 37 tasks in the eval410/evalexcl
  pair became `invalid_task_contract` whatever the agent answered. Two
  explicit allow-lists (ported from the deployed `scorer-set-equal-fix` patch)
  replace that dead end — `canonical_set_equal` for fields whose elements name
  themselves, `ordered_string_vector_equal` for the string-range form of
  `extent_vox`;
- nulls survive the tree TOML: the renderer used to write `""` for a null
  ground-truth value, and the host verifier then compared an answer's `null`
  against a genuine empty string (34 trials, zero full marks). The `__sv_null__`
  sentinel plus a recursive restore in `task_metadata_to_taskspec` close that
  round trip; trees built before this commit need a rebuild to benefit.

Provenance: each build's context was generated at `eval-baseline-release`
(`0b5cf723`, then `d41b21bc`; `git_dirty=false`), archived with its byte count
and sha256 and re-verified on H20 before extraction (SOP §1), then built with
`--network=none` per §2. Full records in [SCORER_RELEASE.md](SCORER_RELEASE.md);
the digest rows are in [IMAGE_INVENTORY.md](IMAGE_INVENTORY.md). All
mirror-path changes ride the same reciprocal-port requirement as the 0924
round.

Open before the next scored run (unchanged by this round): regenerate the cc2d
trees (see above), and re-point any consumer that still pins the scorer by the
H20-local `sci3d-scorer:harbor-0.20-v1` alias (it currently resolves to the
0922-era scorer).
