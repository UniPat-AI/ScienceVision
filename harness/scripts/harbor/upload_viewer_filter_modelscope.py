"""Publish a hand-picked set of 3D assets + QA tasks to a ModelScope dataset.

Unlike ``upload_trajectories_modelscope.py`` (which reshapes *runs*), this script
publishes a small curated **viewer-filter pack**: a few assets and the QA tasks
that ground on them, laid out **by domain**, in the same shape the viewer's
filter panel shows (domain -> {name, note(description), path}).

Layout in the dataset repo (path-in-repo split by domain):

    <repo>/
      embodied/
        assets/cut_dragon.glb  franka_grasp.glb  ipc_cloth.glb  sand_wheel.glb
        qa/genesis_vp.jsonl
      medical/
        assets/image.nii.gz  mask.nii.gz
        qa/medical_spatial.jsonl
      viewer_filter.json        # top-level manifest: domain -> assets[] + qa[]
      README.md                 # auto index

The asset source paths, descriptions, and the QA ground-truth values live in the
SELECTION structure below. Ground truth for the two medical QA items was computed
from the real M3D-RefSeg s0132 mask (nibabel + scipy):
    volume_mm3 = 1_307_903.625   (387_527 fg voxels * 1.5^3 mm)
    cc_6conn   = 4               (one liver body + 3 single-voxel specks)

Auth: reads MODELSCOPE_API_TOKEN from the environment (or ~/.modelscope creds).

Usage:
    python -m scripts.harbor.upload_viewer_filter_modelscope \
        --repo-id tianyi3unipat/BabyVision-v2-Datasets \
        [--stage-dir /tmp/bv2_upload] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

# --- Source roots -----------------------------------------------------------
# glb assets are tracked in the repo under viewer/datasets/genesis/.
REPO_ROOT = Path(__file__).resolve().parents[2]
# M3D-RefSeg lives on the big-data volume (gitignored); fall back to the
# in-repo datasets symlink if present.
M3D_S0132 = Path(
    "/nvme2/matianyi/science-vision/sci3d-big/datasets/M3D-RefSeg/M3D_RefSeg/s0132"
)
if not M3D_S0132.exists():
    M3D_S0132 = REPO_ROOT / "datasets/M3D-RefSeg/M3D_RefSeg/s0132"


# --- The curated selection (assets + QA), grouped by domain -----------------
SELECTION: dict = {
    "embodied": {
        "assets": [
            {
                "src": REPO_ROOT / "viewer/datasets/genesis/cut_dragon.glb",
                "dest": "cut_dragon.glb",
                "name": "Cut Dragon — MPM fracture demo",
                "note": "MPM elastic dragon cut by a cross-shaped cutter, CPIC (animated)",
                "viewer": "genesis",
                "format": "glb",
                "record": "genesis_sim",
            },
            {
                "src": REPO_ROOT / "viewer/datasets/genesis/franka_grasp.glb",
                "dest": "franka_grasp.glb",
                "name": "Franka Grasp — force control demo",
                "note": "Franka arm force-controlled SAP grasp of a rigid cube (animated)",
                "viewer": "genesis",
                "format": "glb",
                "record": "genesis_sim",
            },
            {
                "src": REPO_ROOT / "viewer/datasets/genesis/ipc_cloth.glb",
                "dest": "ipc_cloth.glb",
                "name": "IPC Cloth — FEM cloth demo",
                "note": "IPC: Franka + 2 FEM cloth sheets draping onto a grid of fixed cubes (animated)",
                "viewer": "genesis",
                "format": "glb",
                "record": "genesis_sim",
            },
            {
                "src": REPO_ROOT / "viewer/datasets/genesis/sand_wheel.glb",
                "dest": "sand_wheel.glb",
                "name": "Sand Wheel — MPM granular cascade",
                "note": "MPM Sand emitted downward cascading onto 4 interleaved wheels (animated)",
                "viewer": "genesis",
                "format": "glb",
                "record": "genesis_sim",
            },
        ],
        "qa": [
            {
                "task_id": "genesis_vp/q001",
                "domain": "embodied",
                "question": (
                    "Under a 5 N transverse force applied at the marked vertebra, "
                    "will the dragon fracture into two disconnected pieces within "
                    "200 simulation steps?"
                ),
                "answer_schema": {"kind": "boolean"},
                "ground_truth": {
                    "type": "deterministic_simulation",
                    "value": None,  # to be filled by the deterministic rollout
                    "status": "needs_rollout",
                    "repro": {
                        "solver": "MPM Elastic + CPIC",
                        "dt": 0.004,
                        "substeps": 10,
                        "rollout_steps": 200,
                        "grid_density": 64,
                        "deterministic": True,
                    },
                },
                "asset": "embodied/assets/cut_dragon.glb",
            },
            {
                "task_id": "genesis_vp/q002",
                "domain": "embodied",
                "question": (
                    "After rotating the wheel by 90 degrees clockwise, what fraction "
                    "of the granular sand pile (initially resting at the wheel base) "
                    "remains within a 0.1 m radius of the rotation axis?"
                ),
                "answer_schema": {"kind": "numeric_range"},
                "ground_truth": {
                    "type": "deterministic_simulation",
                    "value": None,  # to be filled by the deterministic rollout
                    "status": "needs_rollout",
                    "repro": {
                        "solver": "MPM Sand (granular)",
                        "dt": 0.003,
                        "substeps": 10,
                        "rollout_steps": 150,
                        "grid_density": 64,
                        "deterministic": True,
                    },
                },
                "asset": "embodied/assets/sand_wheel.glb",
            },
        ],
    },
    "medical": {
        "assets": [
            {
                "src": M3D_S0132 / "ct.nii.gz",
                "dest": "image.nii.gz",
                "name": "Abdominal CT — sample volume nifti",
                "note": (
                    "M3D-RefSeg case s0132 abdominal CT volume "
                    "(labelled region = mild fatty liver)"
                ),
                "viewer": "niivue",
                "format": "nifti",
                "source": "m3d_refseg",
            },
            {
                "src": M3D_S0132 / "mask.nii.gz",
                "dest": "mask.nii.gz",
                "name": "Segmentation mask nifti",
                "note": (
                    "M3D-RefSeg s0132 segmentation mask (label 1 = mild fatty liver); "
                    "1.5 mm isotropic"
                ),
                "viewer": "niivue",
                "format": "nifti",
                "source": "m3d_refseg",
                "overlay_of": "medical/assets/image.nii.gz",
            },
        ],
        "qa": [
            {
                "task_id": "medical_spatial/q001",
                "domain": "medical",
                "question": (
                    "What is the total volume in mm^3 of the labelled region in the "
                    "segmentation mask?"
                ),
                "answer_schema": {"kind": "numeric"},
                "ground_truth": {
                    "type": "mcp_compute_volume",
                    "value": 1307903.625,
                    "unit": "mm^3",
                    "status": "computed",
                    "repro": {
                        "fg_voxels": 387527,
                        "voxel_volume_mm3": 3.375,
                        "voxel_spacing_mm": [1.5, 1.5, 1.5],
                        "tool": "scipy.ndimage / nibabel",
                    },
                },
                "asset": "medical/assets/mask.nii.gz",
            },
            {
                "task_id": "medical_spatial/q002",
                "domain": "medical",
                "question": (
                    "Counting connected components in the segmentation mask "
                    "(6-connectivity), how many distinct regions are present?"
                ),
                "answer_schema": {"kind": "integer"},
                "ground_truth": {
                    "type": "deterministic_topology",
                    "value": 4,
                    "status": "computed",
                    "repro": {
                        "connectivity": 6,
                        "component_sizes_voxels": [387524, 1, 1, 1],
                        "tool": "scipy.ndimage.label (3D, structure=generate_binary_structure(3,1))",
                    },
                },
                "asset": "medical/assets/mask.nii.gz",
            },
        ],
    },
    "industrial": {
        "assets": [
            {
                "src": REPO_ROOT
                / "crawl_data/industrial/khronos_gltf/DamagedHelmet.glb",
                "dest": "DamagedHelmet.glb",
                "name": "Damaged Helmet — Khronos glTF sample",
                "note": (
                    "Khronos DamagedHelmet PBR reference model; lower-front hose "
                    "fittings are a single fused mesh (no per-part nodes)"
                ),
                "viewer": "gltf",
                "format": "glb",
                "source": "khronos_gltf",
                # Decisive comparison view (base64url cam, honored by cam_url.js).
                # Clip planes are slider-only in the viewer, documented separately.
                "view_hint": {
                    "cam": (
                        "eyJwb3NpdGlvbiI6Wy0wLjk5OTIsLTAuMDU3NSwyLjMyMl0sInRhcmdldCI6"
                        "Wy0wLjAwMjksLTAuNjExLDAuNDQwMl0sInVwIjpbMCwxLDBdLCJmb3ZfZGVnIjo0NX0"
                    ),
                    "clip_manual": [
                        {"axis": "z", "slider_pct": 60,
                         "why": "cut away the front visor shell occluding the rear fittings"},
                        {"axis": "y", "slider_pct": -30,
                         "why": "trim the dome behind the connector cluster"},
                    ],
                },
            },
        ],
        "qa": [
            {
                "task_id": "industrial_perception/q001",
                "domain": "industrial",
                "question": (
                    "How many distinct CALIBERS (true bore/diameter) of hose fitting "
                    "are present on the lower-front of the helmet? Also report how many "
                    "fitting positions and how many outward styles you can count. The "
                    "fittings may look like different sizes from a single view -- that "
                    "is a perspective (depth) illusion; resolve it by orbiting to equal "
                    "depth and clipping away the occluding front shell."
                ),
                "answer_schema": {
                    "kind": "structured",
                    "fields": [
                        "n_connectors_visible",
                        "n_distinct_calibers",
                        "n_styles",
                    ],
                },
                "ground_truth": {
                    "type": "perception_judgement",
                    "value": {
                        "n_connectors_visible": 4,
                        "n_distinct_calibers": 1,
                        "n_styles": 2,
                    },
                    "status": "human_annotated",
                    "rationale": (
                        "All hoses are the SAME fitting model placed at different camera "
                        "depths, so they look like different sizes from one view (a "
                        "perspective illusion). Brought to comparable depth -- with the "
                        "occluding front shell clipped away -- the corrugation pitch and "
                        "ring proportion match across all of them: exactly 1 caliber. "
                        "Two outward styles (silver-grey long sleeve / black short ring "
                        "with gold-silver edges) across 4 visible connector positions."
                    ),
                    "evidence": {
                        "decisive_view": (
                            "front/below/left 3-4 with the front visor shell clipped "
                            "away (Cutaway Z+ ~60%)"
                        ),
                        "styles": [
                            "plain metal long sleeve (silver-grey, ~2 corrugations)",
                            "black-body short ring with gold/silver edges (~1 corrugation)",
                        ],
                    },
                },
                "asset": "industrial/assets/DamagedHelmet.glb",
            },
        ],
    },
}

# QA jsonl filename per domain (the in-repo file each domain's QA lands in).
QA_FILENAME = {
    "embodied": "genesis_vp.jsonl",
    "medical": "medical_spatial.jsonl",
    "industrial": "industrial_perception.jsonl",
}


def _stage(stage_dir: Path) -> dict:
    """Copy assets + write QA jsonl by domain; return the viewer-filter manifest."""
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)

    manifest: dict = {"schema_version": "1.0", "domains": {}}
    missing: list[str] = []

    for domain, block in SELECTION.items():
        asset_dir = stage_dir / domain / "assets"
        qa_dir = stage_dir / domain / "qa"
        asset_dir.mkdir(parents=True, exist_ok=True)
        qa_dir.mkdir(parents=True, exist_ok=True)

        asset_entries = []
        for a in block["assets"]:
            src = Path(a["src"])
            dest_rel = f"{domain}/assets/{a['dest']}"
            if not src.exists():
                missing.append(str(src))
                size = None
            else:
                shutil.copy(src, stage_dir / dest_rel)
                size = (stage_dir / dest_rel).stat().st_size
            entry = {
                "name": a["name"],
                "note": a["note"],
                "path": dest_rel,
                "viewer": a["viewer"],
                "format": a["format"],
            }
            if size is not None:
                entry["size"] = size
            for k in ("record", "source", "overlay_of"):
                if k in a:
                    entry[k] = a[k]
            asset_entries.append(entry)

        # QA jsonl (one file per domain) + manifest qa summary
        qa_file = qa_dir / QA_FILENAME.get(domain, f"{domain}.jsonl")
        qa_summary = []
        with qa_file.open("w") as fh:
            for q in block["qa"]:
                fh.write(json.dumps(q, ensure_ascii=False) + "\n")
                qa_summary.append(
                    {
                        "task_id": q["task_id"],
                        "question": q["question"],
                        "answer_type": q["answer_schema"]["kind"],
                        "gt_method": q["ground_truth"]["type"],
                        "qa_file": str(qa_file.relative_to(stage_dir)),
                        "asset": q["asset"],
                    }
                )

        manifest["domains"][domain] = {
            "assets": asset_entries,
            "qa": qa_summary,
        }

    if missing:
        raise FileNotFoundError(
            "missing source asset(s):\n  " + "\n  ".join(missing)
        )

    (stage_dir / "viewer_filter.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    return manifest


def _write_readme(stage_dir: Path, manifest: dict, repo_id: str) -> None:
    lines = [
        "# BabyVision-v2 — viewer-filter asset + QA pack",
        "",
        "A curated set of 3D assets and the QA tasks grounded on them, laid out by "
        "domain in the same shape as the ScienceVision viewer's filter panel.",
        "",
        "Layout: `<domain>/assets/*` (the 3D files) and `<domain>/qa/*.jsonl` "
        "(one QA record per line). `viewer_filter.json` is the top-level manifest "
        "(domain -> assets[] + qa[]).",
        "",
    ]
    for domain, block in manifest["domains"].items():
        lines.append(f"## {domain}")
        lines.append("")
        lines.append("**Assets**")
        lines.append("")
        for a in block["assets"]:
            sz = f" ({a['size']:,} B)" if "size" in a else ""
            lines.append(f"- `{a['path']}`{sz} — {a['name']}: {a['note']}")
        lines.append("")
        lines.append("**QA**")
        lines.append("")
        lines.append("| task_id | answer | GT | question |")
        lines.append("|---|---|---|---|")
        for q in block["qa"]:
            ql = q["question"].replace("|", "\\|")
            lines.append(
                f"| `{q['task_id']}` | {q['answer_type']} | {q['gt_method']} | {ql} |"
            )
        lines.append("")
    lines.append(
        f"_Uploaded to `{repo_id}` by scripts/harbor/upload_viewer_filter_modelscope.py._"
    )
    (stage_dir / "README.md").write_text("\n".join(lines) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-id", default="tianyi3unipat/BabyVision-v2-Datasets",
                    help="org/name of the target ModelScope dataset")
    ap.add_argument("--stage-dir", type=Path, default=Path("/tmp/bv2_upload"))
    ap.add_argument("--path-in-repo", default="", help="subdir within the dataset")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="stage locally and print the plan; do not upload",
    )
    args = ap.parse_args()

    manifest = _stage(args.stage_dir)
    _write_readme(args.stage_dir, manifest, args.repo_id)

    files = sorted(p for p in args.stage_dir.rglob("*") if p.is_file())
    total_bytes = sum(p.stat().st_size for p in files)
    print(f"staged {len(files)} file(s), {total_bytes:,} B under {args.stage_dir}")
    for p in files:
        print(f"  {p.relative_to(args.stage_dir)}  ({p.stat().st_size:,} B)")
    print()
    for domain, block in manifest["domains"].items():
        print(f"[{domain}] {len(block['assets'])} asset(s), {len(block['qa'])} qa")
        for q in block["qa"]:
            print(f"    {q['task_id']}  {q['answer_type']:<13} gt={q['gt_method']}")

    if args.dry_run:
        print("\ndry-run: not uploading.")
        return

    token = os.environ.get("MODELSCOPE_API_TOKEN")
    from modelscope.hub.api import HubApi

    api = HubApi()
    # NOTE: api.login(access_token=...) returns 400 for this token type on the
    # current ModelScope endpoint; upload_folder accepts the token directly via
    # token=, which authenticates fine. Pass it through instead of logging in.
    api.upload_folder(
        repo_id=args.repo_id,
        folder_path=str(args.stage_dir),
        path_in_repo=args.path_in_repo,
        repo_type="dataset",
        token=token,
        commit_message="Add industrial perception-trap case (DamagedHelmet caliber QA)",
    )
    print(f"\nuploaded {len(files)} file(s) to dataset {args.repo_id}")


if __name__ == "__main__":
    main()
