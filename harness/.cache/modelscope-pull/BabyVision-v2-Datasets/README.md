# BabyVision-v2 — viewer-filter asset + QA pack

A curated set of 3D assets and the QA tasks grounded on them, laid out by domain in the same shape as the ScienceVision viewer's filter panel.

Layout: `<domain>/assets/*` (the 3D files) and `<domain>/qa/*.jsonl` (one QA record per line). `viewer_filter.json` is the top-level manifest (domain -> assets[] + qa[]).

## embodied

**Assets**

- `embodied/assets/cut_dragon.glb` (7,864,168 B) — Cut Dragon — MPM fracture demo: MPM elastic dragon cut by a cross-shaped cutter, CPIC (animated)
- `embodied/assets/franka_grasp.glb` (1,213,536 B) — Franka Grasp — force control demo: Franka arm force-controlled SAP grasp of a rigid cube (animated)
- `embodied/assets/ipc_cloth.glb` (1,483,168 B) — IPC Cloth — FEM cloth demo: IPC: Franka + 2 FEM cloth sheets draping onto a grid of fixed cubes (animated)
- `embodied/assets/sand_wheel.glb` (7,675,092 B) — Sand Wheel — MPM granular cascade: MPM Sand emitted downward cascading onto 4 interleaved wheels (animated)

**QA**

| task_id | answer | GT | question |
|---|---|---|---|
| `genesis_vp/q001` | boolean | deterministic_simulation | Under a 5 N transverse force applied at the marked vertebra, will the dragon fracture into two disconnected pieces within 200 simulation steps? |
| `genesis_vp/q002` | numeric_range | deterministic_simulation | After rotating the wheel by 90 degrees clockwise, what fraction of the granular sand pile (initially resting at the wheel base) remains within a 0.1 m radius of the rotation axis? |

## medical

**Assets**

- `medical/assets/image.nii.gz` (7,578,831 B) — Abdominal CT — sample volume nifti: M3D-RefSeg case s0132 abdominal CT volume (labelled region = mild fatty liver)
- `medical/assets/mask.nii.gz` (90,062 B) — Segmentation mask nifti: M3D-RefSeg s0132 segmentation mask (label 1 = mild fatty liver); 1.5 mm isotropic

**QA**

| task_id | answer | GT | question |
|---|---|---|---|
| `medical_spatial/q001` | numeric | mcp_compute_volume | What is the total volume in mm^3 of the labelled region in the segmentation mask? |
| `medical_spatial/q002` | integer | deterministic_topology | Counting connected components in the segmentation mask (6-connectivity), how many distinct regions are present? |

## astronomy

**Assets**

- `astronomy/assets/saturn_from_earth_2026_conjunction.json` (14,882 B) — Saturn @ 2026 conjunction — Earth observer ephemeris: JPL HORIZONS observer table (Saturn from Earth, 2026-Mar-25 conjunction); sub-observer latitude B drives ring tilt
- `astronomy/assets/saturn_from_mars_2026_conjunction.json` (12,249 B) — Saturn @ 2026 conjunction — Mars observer ephemeris: JPL HORIZONS observer table (Saturn from Mars, same epoch); used for the multi-view B-angle consistency task
- `astronomy/assets/2k_saturn.jpg` (199,916 B) — Saturn body texture (2k): Equirectangular Saturn body map for the saturn viewer
- `astronomy/assets/2k_saturn_ring_alpha.png` (12,119 B) — Saturn ring alpha (2k): Radial ring alpha/colour map for the saturn viewer

**QA**

| task_id | answer | GT | question |
|---|---|---|---|
| `astronomy/q001` | camera_pose | needs_expert_annotation | Frame a view of Saturn at its 2026 conjunction that best exposes the ring-plane orientation relative to the Earth line of sight while keeping Saturn centred. Report the camera pose. |
| `astronomy/q002` | categorical | computed | As seen from Earth at Saturn's 2026 conjunction, is the ring plane open (face-on), edge-on, or in an intermediate orientation? |
| `astronomy/q003` | visual_annotation | needs_expert_annotation | On the rendered Saturn-at-conjunction scene, annotate the ring-plane line and label its inclination relative to the Earth line of sight. |
| `astronomy/q004` | structured | computed | Saturn's apparent ring-opening angle (sub-observer latitude B) is observer-dependent. Measure B from Earth and from Mars at the 2026 conjunction and report both, and whether they agree to within 1 degree. |
| `astronomy/q005` | tool_trace | needs_expert_annotation | Using the fewest viewer camera operations, align the view with the Earth line of sight at Saturn's conjunction to demonstrate ring visibility, and return the final view. |
| `astronomy/q006` | composite_ring_geometry | partially_computed | Integrated ring-geometry challenge at Saturn's 2026 conjunction: (1) frame the ring-exposing viewpoint, (2) measure the ring-opening angle B and verify it against a second observer frame, (3) classify ring visibility, and (4) annotate the ring plane relative to the line of sight. Report all sub-answers. |

_Uploaded to `tianyi3unipat/BabyVision-v2-Datasets` by scripts/harbor/upload_viewer_filter_modelscope.py._
