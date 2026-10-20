# Eval baselines — the 6-way matrix

Authoritative description of the baselines the scale-v1 / sample50 evals run.
Sibling docs: `PILOT_2D_RUNBOOK.md` (run commands for the 2D route),
`HARNESS_COMPARISON.md` (the specialized-scaffold-vs-generic-agent contrast),
`ADAPTER_sci3d.md` / `VERIFY_sci3d.md` (agent + verifier adapters).

## The matrix

Six baselines, in the fixed order of `run_end2end.BASELINES` and
`build_sample_trees.REUSE_BASELINES`:

| # | label | agent env | what the agent can see |
|---|-------|-----------|------------------------|
| 1 | `cc` | `claude-code` | the raw 3D file at `/data/<file>`, read with bash + Python (nibabel / trimesh / numpy / scipy / scikit-image / Pillow / matplotlib). No MCP. |
| 2 | `ccmcp` | `claude-code-mcp` | the same raw 3D file **plus** the `sciencevision` MCP server (inspect / render / segment / `sv_build_*_url` viewer links). |
| 3 | `cc2d-1` | `claude-code-2d` | **1** pre-rendered PNG view. No 3D file, no MCP, no 3D libs in the image. |
| 4 | `cc2d-3` | `claude-code-2d` | **3** pre-rendered PNG views. |
| 5 | `cc2d-6` | `claude-code-2d` | **6** pre-rendered PNG views. |
| 6 | `cc2d-12` | `claude-code-2d` | **12** pre-rendered PNG views. |

A seventh route, `host` (the in-process `Sci3dAgent` scaffold), is wired and
staged but **not** in the default matrix — opt in with
`BASELINES="host" bash scripts/harbor/run_sample50_staged.sh`. It exists for the
specialized-scaffold contrast in `HARNESS_COMPARISON.md`, not for the
information-access ablation the six share.

A tools-only Codex route, `3d-mcp`, is wired as its own `run_end2end` matrix
(`--matrix 3d-mcp`) and is **not** in the default six. It is `ccmcp`'s
worker-backed MCP surface with Codex's shell tool disabled (the adapter appends
`--disable shell_tool`), so no file-access path remains and every observation
must come through MCP tools. Launch it into a finished six-baseline jobs root —
the freshness check is scoped per matrix — and read it against that run's own
`job-ccmcp`: task trees, worker image, and task set are identical by
construction, so ccmcp↔3d-mcp isolates the shell tool's contribution.

Read as two orthogonal ablations over one shared harness:

- **`cc` → `ccmcp`** — the MCP ablation. The two images stage the same asset to
  the same path and differ *only* in whether the MCP server is registered.
- **`cc2d-1` → `-3` → `-6` → `-12`** — the view-count ablation. Same tasks, same
  renderer, same seed pose; only the number of views changes. This measures the
  marginal value of "look from one more angle" for a pure-vision agent.

`cc`/`ccmcp` vs the `cc2d-*` family is the third contrast: structured 3D access
vs pixels only.

## Held constant across all six

Everything except "what the agent can see" is shared, which is what makes the
matrix a clean control:

- **Tasks** — the same QA-Gen records, staged per-baseline by
  `build_harbor_tasks.build_one`. `build_sample_trees.py` hardlinks (`cp -al`)
  the per-task dirs across trees rather than re-staging, so the cc2d render pass
  (~2 h for the 1567-task full set) is paid once.
- **Agent** — harbor's built-in `--agent claude-code`, `--model claude-opus-5`.
- **Verifier** — `harbor_adapters.sci3d.verifier:Sci3dVerifier`, one verifier
  with two answer sources: `_verify_translate.answer_json_to_trajview` prefers
  `sci3d_answer.json` and falls back to the last agent message in harbor's
  `trajectory.json`, so every route is scored on the same rails.
- **Reward** — deterministic `answer_score`: strict JSON schema validation,
  binary rubric items, and frozen weighted aggregation. Measurement fields carry
  a per-field band derived from the ground truth's own precision — see
  [Measurement tolerances](#measurement-tolerances). Process/tool-policy signals
  are reported independently and never change leaderboard reward.
- **Judge** — no model call on the default scoring path. `llm_judge` and
  `process_judge` are optional `shadow_*` diagnostics only when a task declares
  them explicitly; they cannot overwrite `answer_score`.
- **Timeouts** — `--agent-setup-timeout-multiplier 3.0`,
  `--agent-timeout-multiplier 2.0`.

Task sets: **scale-v1** = 1567 source records; **sample50** = 150 source records.
The formal sample50 denominator is currently **130**: 20 docking records remain
`needs_expert_annotation` and are excluded until signed expert GT exists.
Therefore the current six-way formal matrix is 130 × 6 = 780 trials.

## Measurement tolerances

A measurement question is not graded on float equality. Every float measurement
grades with the **closed-loop band agreed with the expert side on 2026-09-23**:
use exact match and let the third decimal place differ — implemented as
`±0.005`, whatever precision the ground truth is written with. It supersedes
the earlier "one decimal place wider than written" rule
(`10 ** (1 - decimals_written)`).

| ground truth | band | family |
|---|---|---|
| a float, any written precision | `±0.005` | `contact_distance_A`, `distance_A`, `farthest_heavy_atom_distance_A`, `*_angle_deg`, … |
| a dihedral | `±0.005`, modulus 360 | `*dihedral*` — rides `circular_tolerance` |
| an int inside a measurement vector | `±1` | e.g. `void_ladder_bottom_up_mm` `[2530, 2481]` |
| `[71, 94, 53]` | `±1` per axis | `point_vox`, `bbox_min_vox`, `bbox_max_vox_inclusive` |
| `[37, 28, 83]` | `±2` per axis | `extent_vox` — a difference of two bbox corners, each ambiguous by one voxel |
| `65` | `±1` | `representative_axial_index` |
| `4` | **exact** | counts and identity sets |

The band is *not* a claim about measurement uncertainty. Both sides read the
same file, so there is no instrument error and no sampling error; the only
uncertainty is whether the right atom pair or voxel was picked. A wrong atom
pair moves a distance by 1–3 Å, so the band excludes it by two orders of
magnitude — the band exists to absorb last-digit representation, not to admit
approximations. The expert side notes this strictness keeps distance-question
scores low; that is expected, not a scoring bug.

### Three fields the band is deliberately not given

`scripts/harbor/_tolerance_rules.py` derives the map; `qa_record_to_task` merges
it into `scorer.tolerance`. Three omissions are load-bearing, and each would be a
*silent* failure rather than an error:

- **Counts and identity sets get no entry at all.** Declaring a tolerance on an
  integer field moves `_default_comparator` past its `isinstance(expected, int)`
  branch, silently converting `integer_equal` into a float `abs_tolerance`.
  "Tolerance zero" is spelled by *omitting the key*, never by writing `0`. The
  two integer fields where that swap is intended are named one by one in
  `_INT_TOLERANCE`.
- **A numeric list must get one.** A list field with no tolerance is not graded
  strictly — it becomes `missing_tolerance:<field>`, `invalid_task_contract`,
  `eligible: false`, reward 0.0 whatever the agent answered.
- **A field that cannot be compared numerically is left alone.** A list of
  strings (`screenshots`) would be *made eligible* by attaching a band and then
  fail every comparison, turning a loud contract error into a quiet zero.

### Point-in-mask for medical overlays

The "a voxel index that lies inside the lesion" question is a membership
question, and the scorer now carries `voxel_member_of_set` for it: the region
ships inside the rubric item as a bit-packed blob
(`scripts/harbor/_membership_rules.py` packs it at build time, so the scorer
image stays stdlib-only), an out-of-bounds answer is a wrong answer, and a
malformed region fails closed as `invalid_member_set`. Records opt in by
declaring `ground_truth.method == "radiologist_mask_membership"` (or a
`mask_path` + `mask_id` pair in the value); nothing else changes.

The v9 `med_lesion_point` tasks keep the `±1` voxel band this round: of the 50
v9 medical tasks, only the 25 `med_ct_geometry` ones ship a `mask.nii.gz`
(bbox/extent answers), and the point questions run on native CT grids whose
masks we do not hold. Report medical numbers with that caveat until the family
is re-derived on a masked corpus.

A ground truth that is prose (14 tasks) is outside this scheme entirely: it has
no numeric field to band, and those tasks are `evaluation_ready: false` upstream.

## Image plumbing

`_agent_envs.py` is the single source of truth: each `AgentEnv` bundles a
Dockerfile body with a *stager* (what gets copied into `environment/`) and an
*instruction_rewriter* (how the prose instruction describes it). All three
container images are `node:20-slim` + the `claude` CLI + an `apk` stub that
routes installs through npm; they differ only in what else is inside:

| image | extra contents |
|-------|----------------|
| `sci3d-claude-code-2d` | nothing. **No 3D libs at all** — a leaked 3D file would still be unreadable. |
| `sci3d-claude-code` | python3 + nibabel / trimesh / numpy / scipy / scikit-image / Pillow / matplotlib |
| `sci3d-claude-code-mcp` | the above + the repo installed editable + `mcp` / `fastmcp`, so `sciencevision-mcp` is a console script; registered via harbor's native `--mcp-config` (user-scoped, no trust dialog) |

⚠️ That last clause describes the **harbor path only**. The image carrying the
server is not the same thing as the CLI registering it: a hand-rolled
`docker run` of a `-mcp` image has no `--mcp-config` and the CLI waits for a
confirmation that never comes, so the run silently proceeds as `cc`. See
`DOCKER_IMAGES.md` §3.5a.

Stagers: `cc`/`ccmcp` use `_stage_assets` (`COPY asset/ /data/`); `cc2d-*` uses
`_stage_rendered_views` (`COPY views/ /data/`) and **never** stages the 3D file.
For an eligible eval task, `cc`/`ccmcp` also fail closed if any declared asset
or medical mask is missing.

---

# How the cc2d baselines choose their images

This is the part with the most design decisions in it, so it gets its own
section. All of it happens **offline, at task-generation time**, on H20 where
EGL is available — the agent never renders anything and never sees the source
geometry.

## The pipeline

```
QA-Gen record
  └─ qa_gen_to_tasks.qa_record_to_task     → task dict (asset_path, asset_format,
     (task["n_views"] = 1|3|6|12)            mask_path, domain, …)
      └─ build_harbor_tasks._stage_rendered_views(task, env_dir)
          ├─ _resolve_best_slices(task)                    ← optional NIfTI hint
          ├─ _multiview.plan_views(task, n_views=…)         ← THE PLAN
          │     → [(tool_name, args), …]  length ≈ n_views
          ├─ for each: REGISTRY[tool].handler(args) → envelope → PNG bytes
          │     → environment/views/view_00.png … view_NN.png
          ├─ decode / nonblank / count / ROI / leakage gates
          ├─ environment/render_manifest.json (source + PNG hashes, provenance)
          └─ _instruction_with_rendered_views(...)          ← what the agent reads
```

Two properties are enforced by construction rather than by convention:

1. **No raw-asset leakage.** The stager writes only `views/`. Verify with
   `ls <task>/environment/asset` — it must not exist.
2. **No `asset_path` in the instruction.** `_instruction_with_rendered_views`
   deliberately emits a `sci3d` block containing `views`, `modality`, and
   `no_asset_access: true`, and *no* `asset_path` key — so even an agent that
   parses the block cannot find a file to open.
3. **Fail closed.** Every source asset must produce exactly N decodable,
   nonblank PNGs with provenance. Medical overlays additionally require at
   least `max(64, 0.1% × image_pixels)` mask pixels in one view. Any failure
   raises `RenderVisibilityError`; the task is not runnable.
4. **Multi-asset completeness.** Comparison tasks generate N views for every
   bound asset, with asset IDs retained in the instruction and manifest.

These are technical release gates, not a proof that the answer is visible.
A passing manifest therefore records `visibility_status: technical_pass`,
`answer_visibility_status: pending_blind_audit`, and
`blind_audit_status: required`. Until a blind reviewer has passed the family,
report the result as `cc2d-all` (the PNG-channel lower bound), not
`cc2d-visible`. A technical pass must never be relabeled as answer-visible.

Example (`sv-medi-med_ct_lesion-0097`, cc2d-3):

```
Working in the displayed CT volume, on which side of the body (left or right)
does the highlighted lesion lie? Answer left or right.

Views (pre-rendered; the original 3D file is intentionally unavailable):
- /data/view_00.png
- /data/view_01.png
- /data/view_02.png

```sci3d
{"views": ["/data/view_00.png", "/data/view_01.png", "/data/view_02.png"],
 "modality": "nii.gz", "no_asset_access": true}
```
```

Compare the same task under `cc`/`ccmcp`, which instead get
`Asset: /data/qm9_110589.xyz` and `{"asset_path": "/data/qm9_110589.xyz"}`.

## Dispatch: modality decides the *kind* of view

`_multiview.plan_views` branches on `task["asset_format"]`, and the branch
determines whether "more views" means *more camera angles* or *more slices*:

| `asset_format` | renderer | what the N views are |
|----------------|----------|----------------------|
| `nii`, `nii.gz`, `nifti`, `nifti.gz` | `sv_render_nifti_overlay` (when `mask_path` is present) else `sv_render_nifti_slice` | 2D **slices** across 3 anatomical axes (the view protocol does not apply) |
| `las`, `laz` | `sv_render_pointcloud_thumbnail` | **orbit** camera angles (no v9-corpus task carries a pointcloud; the protocol is not applied to this branch) |
| `obj`, `ply`, `stl`, `glb`, `gltf`, `off` (and `domain != physics`) | `sv_render_mesh_thumbnail` | the frozen **named-face** protocol below |
| `step`, `stp`, `iges`, `igs` | `sv_render_mesh_thumbnail`, fed a tessellated mesh | the frozen **named-face** protocol; the stager tessellates the B-rep first (`scripts/harbor/step_mesh.py`) and fails closed (`step_format_unsupported`) for iges/igs |
| `mmcif`, `cif`, `pdb`, `xyz`, `sdf`, `mol`, `mol2` | `sv_render_molecule_thumbnail` | the frozen **named-face** protocol, in the viewer frame (`angle_frame="viewer_y_up"`) |
| physics animations, urdf, raw `dat`/`json` | — | ineligible until a renderer/evidence contract exists; generation fails closed |

`domain != physics` on the mesh branch is deliberate: physics assets are GLB
*rollouts* (animation), so a static orbit of one frame would be misleading.
Those are deferred rather than rendered wrong.

## The frozen view protocol (`cc2d-views/2`)

Since 2026-09-23 the mesh / CAD / molecule plans are the same camera set
`sv3d_render_canonical_views` samples, so a cc2d↔ccmcp difference can be
attributed to "has 3D tools" rather than to "different camera angles". The sets
are nested — more views is strictly more information:

| baseline | views |
|----------|-------|
| `cc2d-1` | `iso` |
| `cc2d-3` | `iso front top` |
| `cc2d-6` | `iso front top right back left` (== ccmcp's canonical default) |
| `cc2d-12` | the six above + `bottom` + five orbit fills at azimuth 30/90/150/210/270°, elevation 20° |

Angles are **viewer-frame (Y-up) spherical degrees** (`azimuth = atan2(d.x, d.z)`,
`elevation = asin(d.y / |d|)`) — the frame `_snapshot_camera._canonical_offset("Y", …)`
and the Mol\*/gltf viewers use. Every planned entry declares
`angle_frame="viewer_y_up"` (the same value the tool schemas advertise). The
mesh renderer resolves named faces through its per-frame direction tables (no
shift needed) and shifts orbit-fill azimuths by −90° for Z-up assets
(`.obj/.ply/.stl/.off` and the tessellated CAD `.ply`) — without that shift a
fill landed ~90° off; the Y-up branch is already the viewer frame. The molecule
renderer applies the proper (det = +1) `(x, y, z) → (x, −z, y)` rotation plus
the −90° azimuth shift itself (`sci3d_agent/mcp/tools/_render/canonical_views.py`).
The planner's copy of the table is `scripts/harbor/view_protocol.py`; tests hold
the two equal, equal to the viewer's own sampling directions, and the mesh fill
conversion equal to the mapped direction.

`seed_pose` no longer shifts these plans — the named faces are what ccmcp
renders — and only affects the legacy orbit fallback used for view counts the
protocol does not define (anything outside 1/3/6/12).

`render_manifest.json` records `scheme: "cc2d-views/2"`, `angle_frame:
"viewer_y_up"` and each view's name, so a consumer can verify alignment without
re-deriving it. Camera framing stays automatic: target at the bounding-box
centre, `distance = max(diag × 1.4, 1e-3)`, perspective fov 45°, with
`znear`/`zfar` scaled to that distance (`max(1e-4, d × 0.01)` / `max(100 d, 1)`)
— pyrender's 0.05/100 defaults clipped every asset whose bbox diagonal was under
~0.036, which is what blanked 11 small CAD parts. Both pyrender call sites (the
cc2d renderer and the MCP session renderer behind `sv3d_render*`) go through
`_render.common.build_perspective_camera`.

The molecular renderer is **adaptive by structure size** — an important detail,
because "the same views" produce visually very different images across domains:

- **≤ 300 atoms** (chemistry: QM9 `xyz`, COD `cif`) → element-colored
  ball-and-stick, sphere radii from covalent radii, bonds inferred by distance
  (`dist < (r_i + r_j) × 1.30`, an O(n²) search capped at 300 atoms).
- **> 300 atoms** (biology: RCSB `mmcif` proteins/complexes) → CPK spheres only,
  no bonds, polymer downsampled to at most **8000** atoms so the overall fold
  stays readable and the render stays bounded. **HETATM atoms are always kept**:
  the budget applies to polymer sampling only, so a ligand or cofactor — what a
  binding-geometry question asks about — cannot be sampled away. The envelope
  reports `n_hetatm` / `n_hetatm_rendered`.

Parsers are per-format: `gemmi.read_structure` for mmCIF/PDB, a
dependency-free reader for QM9 `xyz`, and fractional→Cartesian conversion for
crystallographic `cif`.

## Render exclusions are receipts, not silence

A cc2d task whose render gate fails is still fail-closed — the tree is removed
and no unchecked tree is substituted — but the failure is now recorded instead
of aborting the batch. `build_one` writes `<out>/<task_id>.cc2d_excluded.json`
(`failure_reason` = the gate's code) before removing the tree; the batch
runners (`qa_gen_to_tasks`, `run_end2end`) turn the *expected* codes
(`CC2D_EXPECTED_EXCLUSION_CODES`: no renderer for the modality, ROI/plan
unavailable, missing CAD kernel) into a tree-local `cc2d_exclusions.jsonl`.
`--2d` trees may then be short of the eligible id set exactly by that receipt:
the tree audits (`build_v21_ev2_trees.audit`,
`run_end2end._require_exact_task_tree`) accept the explained gap, reject an
unexplained one, and reject a receipt that contradicts a built tree. Real
renderer faults (`renderer_exception`, no PNG, leaks) still abort the batch.

## Slice plans (NIfTI / medical)

Here "more views" means more slices. Without a mask, `_nifti_fractions(n)` uses
the fixed fraction plan below. With a mask, the plan is ROI-aware:

| baseline | plan | count |
|----------|------|-------|
| `cc2d-1` | axial maximum-mask-area slice | 1 |
| `cc2d-3` | maximum-mask-area slice on each axis | 3 |
| `cc2d-6` | maximum-mask-area + adjacent slice on each axis; extent tasks use bbox first/last slices | 6 |
| `cc2d-12` | bbox/maximum-area/neighbor coverage on each axis | 12 |

`fraction` is resolved against the actual array dimension as
`index = round(fraction × (dim - 1))`. The axis convention is the one locked
repo-wide: **sagittal = 0, coronal = 1, axial = 2** in numpy array order.

Note the structure of the escalation: 1 → 3 buys **axes** (one plane becomes all
three), and 3 → 6 → 12 buys **depth along each axis**. It is not a uniform
"more of the same", and the two halves of the curve should be read differently.

When the task has a `mask_path` (a lesion / anatomy label), the renderer is
`sv_render_nifti_overlay`, which composites the label over the grayscale slice
at `alpha = 0.4` with the `jet` colormap — so the thing the question asks about
is actually visible. Without a mask it falls back to `sv_render_nifti_slice`.
Intensity windowing is automatic: the 1st–99th percentile of the slice.

### The `best_slices` and extent contract

`_resolve_best_slices` replaces the mid-slice guess with the
*most informative* slice: it calls `sv_inspect_nifti_mask_pair`, which for each
label picks, per axis, the slice with the largest label area
(`argmax` of the per-slice mask voxel sum). The resolver now accepts the full
NIfTI format family, including scale-v1's `nii.gz`, and carries shape + mask bbox
to the planner. Extent questions use bbox endpoints where the view budget allows.
The manifest records each axis/index and the measured mask-pixel gate result.

## Answer-visible release SOP

For each new task family, freeze a label-blind evidence contract before
rendering. The contract may use ROI, central-bond, ligand, or asset identity to
frame the image, but may not use the answer label. After technical gates pass,
a reviewer sees only instruction + PNGs, identifies the evidence, and answers;
that answer is compared by the same deterministic rubric. Audit every item for
a new family, then at least 10% after the policy stabilizes. Any failure rolls
back the family render policy. Only audited manifests belong in a
`cc2d-visible` table.

## Two resolution caveats

`size=512` is passed for every view, but only some renderers honor it:

- **NIfTI slices ignore `size`** — it participates only in the cache key. The
  PNG is the native slice resolution, so views are small and *anisotropic across
  axes*: `sv-medi-med_ct_lesion-0097` yields 125×128, and
  `sv-medi-med_ct_lesion-0015` yields 255×517 for sagittal/coronal but 255×255
  for axial.
- **Molecule views come out 394×394**, not 512 — matplotlib's
  `bbox_inches="tight"` trims the figure after sizing.

Neither is a correctness bug, but the 2D baselines are not all being fed
512×512, and a medical view can be ~1/4 the linear resolution of a chemistry
one. Worth stating whenever cross-domain cc2d numbers are compared.

## Failure handling

The stager no longer emits `views_missing`. Unsupported modality, missing source,
renderer exception, short view count, invalid/blank PNG, raw-asset leakage,
missing provenance, failed multi-asset count, or failed medical ROI coverage all
produce a failed `render_manifest.json` and raise `RenderVisibilityError`.
Regenerate the staged trees with this code before running; trees produced before
this gate are not valid evidence for the deterministic leaderboard.

## Reproducing / verifying a tree

```bash
# generation MUST run in the mcp/GL env (pyrender + EGL), not the harbor venv
python -m scripts.harbor.qa_gen_to_tasks \
    --jsonl <records>.jsonl --out <tree> --asset-root "$(pwd)" \
    --containerized --agent-env claude-code-2d

# invariants
ls <tree>/*/environment/views/*.png | head
find <tree> -name render_manifest.json | head
ls <tree>/*/environment/asset 2>/dev/null && echo "LEAK" || echo "OK: no asset dir"
grep -L no_asset_access <tree>/*/instruction.md   # should print nothing
```
