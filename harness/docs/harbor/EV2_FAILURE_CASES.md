# ev2 Pilot — Failure Cases & Per-Baseline Failure Modes

**Scope:** 15 3D-grounded QA tasks × 3 harbor baselines (`cc` / `cc2d` / `ccmcp`) = 45 deployed cells. Scores are the tiered best per cell (rerun non-degraded > original rescored > degraded), identical to the leaderboard in `docs/harbor/EV2_THREEWAY_REPORT.md`.

**Companion:** per-question trajectories are visualized live in the viewer `#qagen` → **EV2_Pilot** set (bundle `viewer/verify_ev2_pilot.json`).

## 0. Failure overview

| baseline | input to agent | failures / 15 | breakdown |
|---|---|--:|---|
| `cc` | Claude Code (plain, raw 3D file) | **6** | wrong:4 refused:1 errored:1 |
| `cc2d` | Claude Code + 2D multiview (PNGs only) | **11** | wrong:10 refused:1 |
| `ccmcp` | Claude Code + MCP (raw 3D + sci3d tools) | **4** | wrong:3 refused:1 |

## 1. All failure cases

Grouped by task; one row per failing baseline. Rationale is the verbatim judge rationale (llm_judge) where present.

| task (domain) | baseline | status | reward | llm_judge | judge rationale (verbatim) | answer snippet | tier |
|---|---|---|--:|--:|---|---|---|
| sv-biol-docking-pose-plausibility-01 (biology) | `cc2d` | **wrong** | 0.107 | 0.00 | The agent declined to evaluate the pose and provided no assessment of geometric plausibility or key interactions; without access to the structure or image, it … | The working directory is empty, and no image or structure was included in our conversatio… | rescored |
| sv-biol-docking-pose-plausibility-02 (biology) | `cc` | **wrong** | 0.217 | 0.15 | Agent correctly described native XK2 geometry but failed the core task: refused to locate/compare the decoy poses and never identified decoy_08 as least plausi… | ## The pose files aren't here — but the native geometry checks out **Data availability:**… | rescored |
| sv-biol-docking-pose-plausibility-02 (biology) | `cc2d` | **wrong** | 0.107 | 0.00 | The agent failed to provide any structural analysis of ligand poses and instead claimed the required data files do not exist; the ground truth identifies "nati… | I've done an exhaustive search of the filesystem, and I have to report a blocker rather t… | rescored |
| sv-biol-docking-pose-plausibility-02 (biology) | `ccmcp` | **wrong** | 0.021 | 0.00 | The agent declined to answer the core question by claiming pose data doesn't exist, but the ground truth definitively shows "native" is most plausible and "dec… | I need to flag a real problem with this question before attempting an answer. ## The refe… | rescored |
| sv-biol-molecular-collision-check-03 (biology) | `cc2d` | **wrong** | 0.112 | 0.00 | The agent failed to provide a structured answer with the required fields (atom_1, atom_2, clash_present, min_distance_A); instead, it produced explanatory text… | The working directory `/app` is empty, and no structure was included in our conversation … | rescored |
| sv-biol-molecular-collision-check-04 (biology) | `cc` | **wrong** | 0.243 | 0.15 | Agent's answer directly contradicts ground truth (identifies unsatisfied polar group when ground truth states none exist); while spatial analysis of O14 contac… | ## Analysis: buried polar group without H-bond compensation I analyzed the bound ligand *… | rescored |
| sv-biol-molecular-collision-check-04 (biology) | `cc2d` | **wrong** | 0.112 | 0.00 | The agent failed to provide a structural analysis: it only requested data without analyzing any structure, whereas the ground truth clearly indicates that NO u… | The working directory (`/app`) is empty — there's no structure file, PDB, or docking outp… | rescored |
| sv-biol-molecular-collision-check-04 (biology) | `ccmcp` | **wrong** | 0.214 | 0.10 | The agent's answer directly contradicts the ground truth conclusion (claims YES vs ground truth's NO); while geometric analysis of O14 distances appears detail… | ## Answer **Yes — the aryl-ether oxygen (O14) of carazolol is a polar group buried in a h… | rescored |
| sv-chem-chirality-07 (chemistry) | `cc2d` | **wrong** | 0.115 | 0.00 | The agent failed to engage with the task at all—it did not analyze any 3D structure, identify the chiral center C2, or assign the R configuration; instead, it … | There's no molecule available to analyze. The working directory (`/app`) is empty, no str… | rescored |
| sv-chem-conformation-01 (chemistry) | `cc2d` | **wrong** | 0.100 | 0.00 | The agent failed to engage with the 3D structure and instead requested the data be provided, when the task explicitly states "the displayed conformer" was prov… | I don't have access to any displayed conformer — no image, structure file, or molecular d… | rescored |
| sv-chem-conformation-02 (chemistry) | `cc2d` | **wrong** | 0.100 | 0.00 | The agent did not attempt to analyze a 3D structure and instead asked for input, completely failing to engage with the task as framed (which presumed a display… | I don't have any molecule or conformer in front of me — no image, structure file, SMILES … | rescored |
| sv-chem-crystal-packing-03 (chemistry) | `cc` | **wrong** | 0.141 | 0.00 | The agent's answer directly contradicts the ground truth: it claims van der Waals packing dominates with no intermolecular H-bonding, whereas the ground truth … | ## Packing analysis of COD 1501802 This is the dihydroxylated C28 steroid (**C₂₈H₄₀O₃**) … | rescored |
| sv-chem-crystal-packing-03 (chemistry) | `cc2d` | **wrong** | 0.107 | 0.00 | The agent failed to engage with the 3D structure entirely, claiming no structure was provided and offering only generic educational guidance instead of analyzi… | I don't have access to any displayed structure — there's no image, CIF, PDB, or other str… | rescored |
| sv-chem-crystal-packing-04 (chemistry) | `cc2d` | **wrong** | 0.100 | 0.00 | The agent failed to analyze the crystal structure entirely, instead asking for the structure to be provided; it did not engage with any 3D asset and provided n… | The working directory (`/app`) is empty, and nothing was attached to your message — so th… | rescored |
| sv-chem-optimization-vector-06 (chemistry) | `cc` | **refused** | 0.100 | 0.00 | The agent did not attempt to analyze the 3D structure and instead returned an API error; this represents a complete failure to engage with the structural analy… | API Error: Claude Code is unable to respond to this request, which appears to violate our… | rescored |
| sv-chem-optimization-vector-06 (chemistry) | `cc2d` | **refused** | 0.100 | 0.00 | The agent did not provide an answer to the chemistry question; instead, it returned an API error, failing to engage with the 3D structure analysis required to … | API Error: Claude Code is unable to respond to this request, which appears to violate our… | rescored |
| sv-chem-optimization-vector-06 (chemistry) | `ccmcp` | **refused** | 0.101 | 0.00 | The agent returned an API error and failed to engage with the 3D structure or provide any substantive answer to the chemistry question about substitution vecto… | API Error: Claude Code is unable to respond to this request, which appears to violate our… | rescored |
| sv-medi-lesion-3d-extent-03 (medical) | `cc` | **wrong** | 0.121 | 0.05 | The agent reported the full image dimensions [34, 52, 35] mm and 35 slices instead of the actual hippocampal tissue bounding extent [22, 41, 26] mm and 26 slic… | The tissue foreground fills the box at every threshold — even at the 25th-percentile inte… | rescored |
| sv-medi-lesion-3d-extent-03 (medical) | `cc2d` | **wrong** | 0.246 | 0.15 | The agent correctly identified anatomical axes and methodology but systematically overestimated all three bounding box dimensions (34 vs 22, 52 vs 41, 35 vs 26… | I derived the extents from the three orthogonal projection images, which are cropped to t… | rescored |
| sv-medi-lesion-3d-extent-03 (medical) | `ccmcp` | **wrong** | 0.236 | 0.15 | The agent reported bounding extent of ~34×52×35 mm and 35 axial slices spanned, but the ground truth specifies [22, 41, 26] mm and 26 slices; the agent's estim… | ## Result **Important caveat first:** the provided file `hippocampus_003.nii.gz` is the *… | rescored |
| sv-medi-mri-arbitrary-plane-02 (medical) | `cc` | **errored** | 0.104 | 0.00 | The agent provides only a partial initialization message ("Numpy installed. Now nibabel:") and completely fails to provide the required plane normal vector, co… | Numpy installed. Now nibabel: | rescored |

## 2. Per-baseline failure modes

### `cc` — Claude Code (plain, raw 3D file)

*6/15 failed. Tools seen across failing trajectories: `Bash`×5, `Read`×3, `TaskOutput`×1.*

- **No 3D libraries → NIfTI is opaque.** The plain image ships the raw `.nii.gz` but no medical-imaging stack; the agent cannot even `file` it reliably (exit 127) and falls back to answering from anatomical priors rather than the voxels — weak on `sv-medi-*` (mri-arbitrary-plane, lesion-extent).
- **Atom-index mislabeling on molecules.** It parses `.mol` coordinates by hand and computes the CIP triple product correctly, but labels the stereocenter by file atom index (`C7`) instead of the ground-truth chemical numbering (`C2`) — method right, label wrong → `llm_judge = 0` (sv-chem-chirality-07).
- **"Asset not staged" false-negative on the docking tasks.** On sv-biol-docking-pose-02 the agent searches the sandbox, concludes the pose files are not present, and declines rather than reasoning from the staged asset — a self-inflicted miss the judge scores 0 even though the ground truth is answerable.
- **Structural zero on the closed-loop process signals.** With no `sv3d_open_scene` / render tools, `process_opened_scene` and `process_rendered` are 0 by construction regardless of answer quality.
- **Setup-timeout / non-zero-exit errors.** The in-container `npm install -g @anthropic-ai/claude-code` bootstrap hit the 360 s `AgentSetupTimeout` in the original run; re-running with a 3× multiplier recovered the trials.

### `cc2d` — Claude Code + 2D multiview (PNGs only)

*11/15 failed. Tools seen across failing trajectories: `Bash`×8, `Read`×1.*

- **Molecular / point-cloud views are missing → blind on chemistry & biology.** The pure-vision image stages only offline-rendered PNGs, and the molecular/point-cloud renderer is deferred (`views_missing`), so the `views/` dir is empty for most `sv-chem-*` / `sv-biol-*` tasks. The agent sees nothing and emits a generic non-answer ('paste the molecule and I will assign…') → the 0.100 process floor.
- **Competitive only where slices actually render.** Medical NIfTI slices do render, which is the one domain where `cc2d` has real evidence and even wins a task (mri-arbitrary-plane-02). Its low chemistry/biology scores are a rendering-pipeline artifact, not intrinsic vision weakness.

### `ccmcp` — Claude Code + MCP (raw 3D + sci3d tools)

*4/15 failed. Tools seen across failing trajectories: `Bash`×3, `mcp__sciencevision__sv_inspect_molecule`×1, `mcp__sciencevision__sv_get_asset`×1, `mcp__sciencevision__sv3d_open_scene`×1, `mcp__sciencevision__sv3d_segment_3d_volume`×1.*

- **Strongest overall, but not immune on hard tasks.** It still fails the hard biology pair (docking-pose-02, molecular-collision-04) that is hard for every baseline, and the medical lesion-extent task.
- **Also declines docking-pose-02 as "data unavailable."** Like `cc`, it reports the pose files are missing from the sandbox and refuses the comparison, despite the ground truth documenting the specific native-vs-decoy poses — the MCP tools do not rescue a task the agent believes has no asset.
- **Judge variance costs it, too.** On chirality-07 the near-identical 'C7 is R' answer was scored 0.60 here but 0.0 for `cc` — single-sample judging is noisy in both directions.
- **Usage-Policy refusal.** optimization-vector-06 (ligand substitution vectors into an acetylcholinesterase subpocket) trips a false-positive refusal on all three baselines, `ccmcp` included → no answer → floor.

## 3. Cross-cutting issues

- **Judge-503 degrade (fixed).** The verifier's LLM judge defaulted to the *dated* id `claude-haiku-4-5-20251001`, which the ALB gateway 503s (channel pricing restriction). When it 503s, both `llm_judge` and `process_judge` degrade to 0 and reward collapses to the 0.100 process floor — a scoring outage masquerading as a result. Fixed by offline-rescoring with the undated `claude-haiku-4-5`; all 45 deployed cells are non-degraded. See `docs/harbor/EV2_THREEWAY_REPORT.md` §5.
- **Single-judge, single-sample variance.** Absolute `llm_judge` values carry real noise (the chirality-07 0.0-vs-0.60 split on identical conclusions). Multi-sample / multi-judge scoring would tighten the per-task numbers.
- **Ground truth is provisional.** Every `task.toml` GT is marked `status = needs_expert_annotation`. A 'wrong' verdict here means 'disagreed with the provisional expert reference under one judge', not a certified error — treat the failure set as *relative* signal between baselines, pending expert sign-off.
- **The 30% process weight structurally favors the tool-equipped baseline.** `process_opened_scene` / `process_rendered` can only fire for `ccmcp`, so part of every `cc` / `cc2d` reward gap is definitional, not answer quality.

---

*Generated by `scripts/harbor/failure_report_ev2.py` (imports the shared helpers in `scripts/harbor/build_ev2_viewer_bundle.py`).*
