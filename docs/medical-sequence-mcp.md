# Medical sequence switching in evaluation

`sv3d_switch_sequence` extends the evaluation MCP with the sequence-switching
capability introduced by [platform PR132](https://github.com/UniPat-AI/BabyVision-v2/pull/132).
It changes the active image inside one NIfTI session. Existing results and
frozen task trees retain their original tool surfaces.

The bundled Codex and Claude Code runtimes retain their existing 73 tools and
add one capability. The separate external HTTP worker retains its 58 tools and
adds one. These profiles have different existing tools and wire schemas; their
catalogs are recorded separately. A task with one public image hides the new
tool. Other viewer families retain their existing surfaces.

## Input contract

Declare each public image in the task's existing `assets` list, using one
`primary` role and any additional `sequence` roles. Each must reference an
actual `.nii` or `.nii.gz` image and use `viewer: "niivue"` when a viewer is
declared. The primary image opens first regardless of attachment order.
The generator stages these public assets and writes the existing
`sciencevision.mcp_task_assets.v1` manifest. Runtime discovery reads that
manifest; it never searches adjacent files or invents missing modalities.

The eval switch currently requires **3D images with the same NIfTI spatial
units** and a shared anatomical world coordinate system. It supports different
dimensions, voxel spacing and affine transforms when the current focus remains
within the target field of view. It does not provide temporal/4D frame switching,
DICOM-series import, or loading embedded JavaScript/base64 volumes from HTML.
The expert viewer's separate first-frame behavior is not an eval capability.

`mask`, reference and ground-truth assets are not sequences. Existing asset
visibility rules still apply: only an explicitly public overlay is staged for
the model. An `R`, `A` or `B` file existing on disk is insufficient to make it
public. Source task questions and answer boundaries are unchanged.

## Calls and state

Open the declared primary with `sv3d_open_scene`, then inspect
`sv3d_get_state`. Multi-image sessions report `sequence` and `sequences`.
Ordinary names come from filenames, for example `T1GD`, `T1`, `T2`, `FLAIR`.
Filename collisions use a deterministic `./` path-qualified identifier. Use
the names in state or the task-specific tool enum.

The wire call retains the runtime's existing `args` wrapper:

```json
{"args":{"session_id":"<opened-session>","sequence":"T2"}}
```

State reports `sequence_focus_world` as a continuous world anchor. Displayed
slice indices select the nearest voxels; this quantization does not move the
stored world anchor. Crosshair and subsequent per-axis slice changes update
the anchor. Camera, mode, clipping and overlay visibility persist. Each target
uses its original intensity window. Public masks are resampled from their
original source with nearest-neighbor labels when grids differ.

A missing, invalid or unavailable target returns an error without replacing
the session. Repeating the returned `replay_input` is deterministic. The
existing eval full reset remains close-and-reopen; this change does not add a
`viewer_reset` tool or invent persisted measurement annotations.

## Generate new bundled-stdio tasks

Use a published sequence-capable bundled image recorded in
[`resources/images.json`](../resources/images.json). Export its actual catalog
for the task's viewer family before generation:

```bash
IMAGE=ghcr.io/unipat-ai/sciencevision-codex-mcp@sha256:98b57812659434bb02393ff250aacdb0d9fce79f05bdec6f888ff189ffa33e2a
docker run --rm --entrypoint sciencevision-mcp "$IMAGE" \
  --surface --viewer-family niivue.v1 > niivue-surface.json
cd harness
python -m scripts.harbor.qa_gen_to_tasks \
  --jsonl approved-medical-tasks.jsonl --out new-stdio-tasks \
  --containerized --agent-env codex-mcp \
  --bundled-mcp-image "$IMAGE" --mcp-surface ../niivue-surface.json
```

`claude-code-mcp` uses the same opt-in flag with its own exact image catalog.
The generator emits the bundled Dockerfile, public asset manifest,
`environment/mcp.stdio.json` and `environment/mcp-runtime-profile.json`.
The profile carries the real image's task-specialized catalog and schema hashes.
Use the emitted stdio config with the native launcher. This is the existing
**3D + Scaffold + Vision MCP** source-access setting, not MCP-only isolation.
The matrix driver's default HTTP config is not silently changed by this opt-in.

Without `--bundled-mcp-image`, generation retains the external HTTP layout.
The default worker is
`ghcr.io/unipat-ai/sciencevision-mcp-worker@sha256:4c8a482d5cc1cce0d04eaa7861f463843fa50c95318100a0db877a828c843d4f`.
Use its own `--surface` output for that
profile. Public sources stay in the private worker, and the emitted per-task
manifest, enum and catalog hashes agree with the worker's actual `tools/list`.
Old worker tags are retained for reproduction and do not gain the new tool.

## Auditable image build inputs

The bundled overlay generator is
`harness/scripts/harbor/sequence_runtime_overlay.py`; it validates the source
fingerprints of the audited bundled base and emits only the changed files plus
`sequence-overlay.json`. `Dockerfile.sequence-overlay` merges these files into
that base without replacing its renderer, agent CLI, dependencies or 73 existing
wire schemas. Its two new shared modules are the same source used by the
external worker.

The exact reusable bundled bases are
`ghcr.io/unipat-ai/sciencevision-codex-mcp@sha256:4de6598960d745f408e8f91050b3b73234416dd5ae86d35bf26637094a32b7ca`
and
`ghcr.io/unipat-ai/sciencevision-claude-code-mcp@sha256:74a05ae987097140b97ca976a406cf272866c9edbb6fa2313e897a25dab36df5`.
The overlay source was built before its eventual Git commit: its content manifest
records the actual base and file hashes; the final source commit is not claimed
as the image's build revision.

The separate `Dockerfile.sequence-worker-overlay` updates the external worker's
reviewed source while retaining its non-root user, dependencies and healthcheck.
The standard full worker Dockerfile and allow-listed builder also include the
new capability when rebuilding from source. Release tags, digests and source
verification belong in the image inventory after an actual build and registry
readback; local candidates do not establish a pullable release.
