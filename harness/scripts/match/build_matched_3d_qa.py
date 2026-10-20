"""Build per-dataset matching JSONs that pair Tier-1 3D-grounded QA items with the
3D file present locally under datasets/.

Availability policy (selectable via --scannet-availability):
  disk     — default. ScanNet train scene counts as present iff its mesh file exists on this filesystem.
  manifest — read --scannet-availability-manifest (one scene_id per line) and treat those as present.
             Use this when train data lives on a remote authoritative host.

Blocklist (always applied if provided via --scannet-blocklist):
  - A scene whose missing list contains "_vh_clean_2.ply" is treated as mesh-absent (no record emitted).
  - Other scenes in the blocklist still emit records but get extra.scannet_missing_files = [...].

Outputs:
  datasets/_matched/scanqa.json
  datasets/_matched/sqa3d.json
  datasets/_matched/m3d-refseg.json
  datasets/_matched/embodiedscan-demo.json   (no QA pairs, asset-only)
  datasets/_matched/index.json

Usage:
  python scripts/match/build_matched_3d_qa.py            # writes JSONs (disk policy)
  python scripts/match/build_matched_3d_qa.py --dry-run  # prints stats only
  python scripts/match/build_matched_3d_qa.py \\
      --scannet-availability manifest \\
      --scannet-availability-manifest datasets/_shared_3d/scannet/_tools/available_train_scenes.txt \\
      --scannet-blocklist datasets/_shared_3d/scannet/_tools/incomplete_train_scenes.json
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]
MESH_CRITICAL_FILE = "_vh_clean_2.ply"


def _paths(repo_root: Path) -> dict[str, Path]:
    return {
        "repo_root": repo_root,
        "datasets": repo_root / "datasets",
        "out_dir": repo_root / "datasets" / "_matched",
        "scanqa_qa_dir": repo_root / "datasets" / "ScanQA" / "data" / "qa" / "ScanQA_v1.0",
        "scannet_test_dir": repo_root / "datasets" / "_shared_3d" / "scannet" / "scans_test",
        "scannet_train_dir": repo_root / "datasets" / "_shared_3d" / "scannet" / "scans",
        "sqa3d_dir": repo_root / "datasets" / "SQA3D" / "sqa_task" / "balanced",
        "m3d_refseg_root": repo_root / "datasets" / "M3D-RefSeg",
        "m3d_seg_0005_dir": repo_root / "datasets" / "M3D-Seg" / "M3D_Seg" / "0005",
        "m3d_seg_root": repo_root / "datasets" / "M3D-Seg" / "M3D_Seg",
        "embodied_demo_dir": repo_root / "datasets" / "EmbodiedScan" / "demo_data" / "openscan",
    }


VIEWER_URL_PREFIX = ""  # viewer is served from repo root


@dataclass(frozen=True)
class MatchRecord:
    qid: str
    dataset: str
    split: str
    scene_or_case_id: str
    question: str
    answer: str | None
    asset_kind: str          # "mesh" | "nifti" | "npy_volume" | "ply"
    path_repo: str           # POSIX, relative to repo root
    url_viewer: str          # leading slash, ready for viewer fetch
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ScannetAvailability:
    mode: str                              # "disk" | "manifest"
    available_train: frozenset[str]        # only meaningful when mode == "manifest"
    blocklist: dict[str, list[str]]        # scene_id -> list of missing file suffixes

    @property
    def policy_label(self) -> str:
        return (
            "strict_present_on_disk"
            if self.mode == "disk"
            else "scannet_train_via_manifest"
        )


def _load_blocklist(path: str | None) -> dict[str, list[str]]:
    if not path:
        return {}
    data = json.loads(Path(path).read_text())
    return {sid: rec.get("missing", []) for sid, rec in data.get("scenes", {}).items()}


def _load_availability_manifest(path: str | None) -> frozenset[str]:
    if not path:
        return frozenset()
    lines = Path(path).read_text().splitlines()
    return frozenset(s.strip() for s in lines if s.strip())


def to_url(path_repo: str) -> str:
    return "/" + path_repo


def asset_present(repo_root: Path, path_repo: str) -> bool:
    p = repo_root / path_repo
    return p.is_file() and p.stat().st_size > 0


def scannet_extra_for_scene(scene_id: str, avail: ScannetAvailability) -> dict[str, Any]:
    """If scene has non-mesh-missing files, return annotation. Mesh-missing already filtered upstream."""
    missing = avail.blocklist.get(scene_id, [])
    if missing and MESH_CRITICAL_FILE not in missing:
        return {"scannet_missing_files": list(missing)}
    return {}


# ---------- ScanQA ----------

SCANQA_FILES = {
    # val first so users land on records that have ground-truth answers
    # (upstream test_* splits ship with no `answers` field — it's a
    # held-out benchmark). Train would be ideal too but its scenes
    # aren't on disk locally; only the test_* scans + val mesh subset
    # are downloaded.
    "val": "ScanQA_v1.0_val.json",
    "test_wo_obj": "ScanQA_v1.0_test_wo_obj.json",
    "test_w_obj": "ScanQA_v1.0_test_w_obj.json",
    "train": "ScanQA_v1.0_train.json",
}


def scannet_mesh_path(paths: dict[str, Path], scene_id: str, avail: ScannetAvailability) -> str | None:
    """Return repo-relative path to scene mesh if available per the active policy, else None."""
    # Test scenes: always disk check (test data lives locally via HF mirror)
    cand_test = paths["scannet_test_dir"] / scene_id / f"{scene_id}_vh_clean_2.ply"
    if cand_test.is_file() and cand_test.stat().st_size > 0:
        return str(cand_test.relative_to(paths["repo_root"]))

    # Train scenes: policy decides
    train_rel = f"datasets/_shared_3d/scannet/scans/{scene_id}/{scene_id}_vh_clean_2.ply"
    if avail.mode == "manifest":
        if scene_id in avail.available_train and MESH_CRITICAL_FILE not in avail.blocklist.get(scene_id, []):
            return train_rel
        return None

    cand_train = paths["scannet_train_dir"] / scene_id / f"{scene_id}_vh_clean_2.ply"
    if cand_train.is_file() and cand_train.stat().st_size > 0:
        return train_rel
    return None


def iter_scanqa(paths: dict[str, Path], avail: ScannetAvailability) -> Iterable[MatchRecord]:
    for split, fname in SCANQA_FILES.items():
        path = paths["scanqa_qa_dir"] / fname
        if not path.is_file():
            continue
        with path.open() as f:
            rows = json.load(f)
        for row in rows:
            scene_id = row["scene_id"]
            mesh = scannet_mesh_path(paths, scene_id, avail)
            if mesh is None:
                continue
            answers = row.get("answers")  # only present in train/val
            answer = answers[0] if answers else None
            yield MatchRecord(
                qid=f"scanqa-{split}-{row['question_id']}",
                dataset="scanqa",
                split=split,
                scene_or_case_id=scene_id,
                question=row["question"],
                answer=answer,
                asset_kind="mesh",
                path_repo=mesh,
                url_viewer=to_url(mesh),
                extra=scannet_extra_for_scene(scene_id, avail),
            )


# ---------- SQA3D ----------

SQA3D_SPLITS = ("train", "val", "test")


def iter_sqa3d(paths: dict[str, Path], avail: ScannetAvailability) -> Iterable[MatchRecord]:
    for split in SQA3D_SPLITS:
        q_path = paths["sqa3d_dir"] / f"v1_balanced_questions_{split}_scannetv2.json"
        a_path = paths["sqa3d_dir"] / f"v1_balanced_sqa_annotations_{split}_scannetv2.json"
        if not (q_path.is_file() and a_path.is_file()):
            continue
        with q_path.open() as f:
            qdata = json.load(f)
        with a_path.open() as f:
            adata = json.load(f)
        ans_by_qid = {a["question_id"]: a for a in adata["annotations"]}
        for q in qdata["questions"]:
            scene_id = q["scene_id"]
            mesh = scannet_mesh_path(paths, scene_id, avail)
            if mesh is None:
                continue
            ann = ans_by_qid.get(q["question_id"], {})
            ans_list = ann.get("answers", [])
            answer = ans_list[0]["answer"] if ans_list else None
            extra = {
                "situation": q.get("situation"),
                "rotation": ann.get("rotation"),
                "position": ann.get("position"),
            }
            extra.update(scannet_extra_for_scene(scene_id, avail))
            yield MatchRecord(
                qid=f"sqa3d-{split}-{q['question_id']}",
                dataset="sqa3d",
                split=split,
                scene_or_case_id=scene_id,
                question=q["question"],
                answer=answer,
                asset_kind="mesh",
                path_repo=mesh,
                url_viewer=to_url(mesh),
                extra=extra,
            )


# ---------- M3D-RefSeg ----------

M3D_REFSEG_SPLITS = {
    "train": "M3D_RefSeg_train.csv",
    "test": "M3D_RefSeg_test.csv",
}


def _load_text_json(paths: dict[str, Path], case_id: str) -> dict[str, str] | None:
    """Return parsed text.json for a RefSeg case, or None on missing/invalid file."""
    p = paths["m3d_refseg_root"] / "M3D_RefSeg" / case_id / "text.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def iter_m3d_refseg(paths: dict[str, Path]) -> Iterable[MatchRecord]:
    text_cache: dict[str, dict[str, str] | None] = {}
    for split, fname in M3D_REFSEG_SPLITS.items():
        csv_path = paths["m3d_refseg_root"] / fname
        if not csv_path.is_file():
            continue
        with csv_path.open() as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                ct_rel = (paths["m3d_refseg_root"] / "M3D_RefSeg" / row["Image"]).relative_to(paths["repo_root"])
                mask_rel = (paths["m3d_refseg_root"] / "M3D_RefSeg" / row["Mask"]).relative_to(paths["repo_root"])
                if not (asset_present(paths["repo_root"], str(ct_rel)) and asset_present(paths["repo_root"], str(mask_rel))):
                    continue
                case_id = row["Image"].split("/", 1)[0]  # e.g. "s0736"
                if case_id not in text_cache:
                    text_cache[case_id] = _load_text_json(paths, case_id)
                tj = text_cache[case_id]
                mid = row.get("Mask_ID")
                yield MatchRecord(
                    qid=f"m3d-refseg-{split}-{case_id}-{i:05d}",
                    dataset="m3d-refseg",
                    split=split,
                    scene_or_case_id=case_id,
                    question=row["Question"],
                    answer=row["Answer"],
                    asset_kind="nifti",
                    path_repo=str(ct_rel),
                    url_viewer=to_url(str(ct_rel)),
                    extra={
                        "mask_path_repo": str(mask_rel),
                        "mask_url_viewer": to_url(str(mask_rel)),
                        "mask_id": mid,
                        "question_type": row.get("Question_Type"),
                        "mask_text": (tj.get(mid) if tj is not None and mid is not None else None),
                        "mask_label_count": (len(tj) if tj is not None else None),
                    },
                )


# ---------- M3D-Seg/0005 (segmentation-only — no QA pairs) ----------


def _load_seg_catalog(catalog_path: Path) -> list[str]:
    """Read a M3D-Seg ``<NNNN>.json`` and return foreground class names in numeric order.

    Background (index ``0``) is dropped. Returns ``[]`` on any I/O or schema error
    so callers always have a safe default.
    """
    if not catalog_path.is_file():
        return []
    try:
        with catalog_path.open() as f:
            catalog = json.load(f)
        labels = catalog.get("labels", {}) or {}
        fg: list[str] = []
        for idx in sorted(labels.keys(), key=lambda k: int(k)):
            if int(idx) == 0:
                continue
            fg.append(labels[idx])
        return fg
    except (OSError, ValueError, KeyError):
        return []


def _seg_synthetic_qa(fg_classes: list[str]) -> tuple[str, str, str]:
    """Render synthetic (question, answer, class_summary) from a class list.

    Empty class list → fallback strings so the viewer still has something to render.
    """
    if not fg_classes:
        return "Multi-class CT segmentation.", "See mask overlay.", ""
    class_summary = " · ".join(f"{i+1}={name}" for i, name in enumerate(fg_classes))
    question = (
        "Segment the following structures in this CT volume: "
        + ", ".join(fg_classes)
        + "."
    )
    answer = f"{len(fg_classes)}-channel mask overlay — {class_summary}"
    return question, answer, class_summary


def iter_m3d_seg_0005(paths: dict[str, Path]) -> Iterable[MatchRecord]:
    """Emit segmentation records for M3D-Seg/0005.

    Upstream defines no QA pairs (it's a pure segmentation task), but
    the dataset's `0005.json` ships a class label catalog. We surface
    those labels as a synthetic Q/A pair so the match3d card has a
    meaningful annotation alongside the dual-pane mask overlay —
    otherwise the chip block is empty and the user just sees an
    unexplained mask.

    Behaviour:
      Converted (NIfTI on disk)  → asset_kind='nifti', renders dual-pane.
      Not converted              → asset_kind='npy_volume', needs_conversion hint.
    """
    if not paths["m3d_seg_0005_dir"].is_dir():
        return

    fg_classes = _load_seg_catalog(paths["m3d_seg_0005_dir"] / "0005.json")
    synthetic_question, synthetic_answer, class_summary = _seg_synthetic_qa(fg_classes)

    derived_root = paths["repo_root"] / "datasets" / "_derived" / "m3d-seg-0005"
    for case_dir in sorted(paths["m3d_seg_0005_dir"].iterdir()):
        if not case_dir.is_dir():
            continue
        image_npy = case_dir / "image.npy"
        if not (image_npy.is_file() and image_npy.stat().st_size > 0):
            continue
        case_id = case_dir.name

        derived_img = derived_root / case_id / "image.nii.gz"
        derived_msk = derived_root / case_id / "mask.nii.gz"
        if derived_img.is_file() and derived_msk.is_file():
            img_rel = str(derived_img.relative_to(paths["repo_root"]))
            msk_rel = str(derived_msk.relative_to(paths["repo_root"]))
            yield MatchRecord(
                qid=f"m3d-seg-0005-{case_id}",
                dataset="m3d-seg-0005",
                split="0005",
                scene_or_case_id=case_id,
                question=synthetic_question,
                answer=synthetic_answer,
                asset_kind="nifti",
                path_repo=img_rel,
                url_viewer=to_url(img_rel),
                extra={
                    "mask_path_repo": msk_rel,
                    "mask_url_viewer": to_url(msk_rel),
                    "no_qa": True,
                    "synthetic_qa": True,
                    "mask_text": class_summary or None,
                    "mask_label_count": (
                        f"{len(fg_classes)} classes" if fg_classes else None
                    ),
                    "derived_from": str(image_npy.relative_to(paths["repo_root"])),
                },
            )
            continue

        # Conversion hasn't happened yet — emit a non-renderable stub so
        # the front-end can flag it instead of silently dropping the case.
        mask_files = sorted(case_dir.glob("mask_*.npz"))
        mask_rel = (
            str(mask_files[0].relative_to(paths["repo_root"]))
            if mask_files and mask_files[0].stat().st_size > 0
            else None
        )
        rel = str(image_npy.relative_to(paths["repo_root"]))
        yield MatchRecord(
            qid=f"m3d-seg-0005-{case_id}",
            dataset="m3d-seg-0005",
            split="0005",
            scene_or_case_id=case_id,
            question=synthetic_question,
            answer=synthetic_answer,
            asset_kind="npy_volume",
            path_repo=rel,
            url_viewer=to_url(rel),
            extra={
                "mask_path_repo": mask_rel,
                "mask_url_viewer": to_url(mask_rel) if mask_rel else None,
                "no_qa": True,
                "synthetic_qa": True,
                "mask_text": class_summary or None,
                "mask_label_count": (
                    f"{len(fg_classes)} classes" if fg_classes else None
                ),
                "needs_conversion": (
                    "Run tools/convert_m3d_seg_npy.py to produce "
                    "datasets/_derived/m3d-seg-0005/<id>/{image,mask}.nii.gz "
                    "for browser preview."
                ),
            },
        )


# ---------- M3D-Seg full (subsets 0000–0024 except 0005) ----------

# 0005 is owned by ``iter_m3d_seg_0005`` (it has a special NIfTI-derived
# render path). Every other subset is handled here as ``npy_volume``
# records — converting all ~5,771 cases to NIfTI is out of scope for the
# matcher and belongs to a dedicated conversion pipeline.
M3D_SEG_EXCLUDED_SUBSETS = frozenset({"0005"})


def iter_m3d_seg_all(paths: dict[str, Path]) -> Iterable[MatchRecord]:
    """Emit synthetic-QA records for every present M3D-Seg subset except 0005.

    Each subset directory must contain a ``<NNNN>.json`` catalog (used for the
    label list) and one case directory per scan, each with an ``image.npy`` and
    ``mask_*.npz``. The class list drives the synthetic Q/A surfaced on the
    viewer card, exactly as for 0005.

    Local disk semantics: a case is emitted iff its ``image.npy`` is present
    and non-empty. On a machine that only mirrors the ScanNet/RefSeg slice
    (e.g. the developer Mac), this iterator yields nothing — by design.
    """
    seg_root = paths["m3d_seg_root"]
    if not seg_root.is_dir():
        return

    for subset_dir in sorted(seg_root.iterdir()):
        if not subset_dir.is_dir():
            continue
        subset_id = subset_dir.name
        if subset_id in M3D_SEG_EXCLUDED_SUBSETS:
            continue

        fg_classes = _load_seg_catalog(subset_dir / f"{subset_id}.json")
        question, answer, class_summary = _seg_synthetic_qa(fg_classes)
        mask_label_count = f"{len(fg_classes)} classes" if fg_classes else None

        for case_dir in sorted(subset_dir.iterdir()):
            if not case_dir.is_dir():
                continue
            image_npy = case_dir / "image.npy"
            if not (image_npy.is_file() and image_npy.stat().st_size > 0):
                continue

            mask_files = sorted(case_dir.glob("mask_*.npz"))
            mask_rel = (
                str(mask_files[0].relative_to(paths["repo_root"]))
                if mask_files and mask_files[0].stat().st_size > 0
                else None
            )
            rel = str(image_npy.relative_to(paths["repo_root"]))
            yield MatchRecord(
                qid=f"m3d-seg-{subset_id}-{case_dir.name}",
                dataset="m3d-seg",
                split=subset_id,
                scene_or_case_id=case_dir.name,
                question=question,
                answer=answer,
                asset_kind="npy_volume",
                path_repo=rel,
                url_viewer=to_url(rel),
                extra={
                    "mask_path_repo": mask_rel,
                    "mask_url_viewer": to_url(mask_rel) if mask_rel else None,
                    "no_qa": True,
                    "synthetic_qa": True,
                    "mask_text": class_summary or None,
                    "mask_label_count": mask_label_count,
                    "needs_conversion": (
                        "Run a npy→nifti conversion pipeline (see "
                        "tools/convert_m3d_seg_npy.py for the 0005 reference) "
                        "to enable browser preview."
                    ),
                },
            )


# ---------- EmbodiedScan demo ----------


def iter_embodiedscan_demo(paths: dict[str, Path]) -> Iterable[MatchRecord]:
    if not paths["embodied_demo_dir"].is_dir():
        return
    for scene_dir in sorted(paths["embodied_demo_dir"].iterdir()):
        if not scene_dir.is_dir():
            continue
        mesh = scene_dir / "mesh.ply"
        if not (mesh.is_file() and mesh.stat().st_size > 0):
            continue
        rel = str(mesh.relative_to(paths["repo_root"]))
        yield MatchRecord(
            qid=f"embodiedscan-demo-{scene_dir.name}",
            dataset="embodiedscan-demo",
            split="demo",
            scene_or_case_id=scene_dir.name,
            question="",
            answer=None,
            asset_kind="ply",
            path_repo=rel,
            url_viewer=to_url(rel),
            extra={"no_qa": True},
        )


# ---------- driver ----------

# Iterators that need ScanNet availability info (scanqa/sqa3d) are wrapped to take (paths, avail).
# Others take (paths) only.

SCANNET_AWARE_ITERATORS = {"scanqa", "sqa3d"}
DATASET_ITERATORS = {
    "scanqa": iter_scanqa,
    "sqa3d": iter_sqa3d,
    "m3d-refseg": iter_m3d_refseg,
    "m3d-seg-0005": iter_m3d_seg_0005,
    "m3d-seg": iter_m3d_seg_all,
    "embodiedscan-demo": iter_embodiedscan_demo,
}


def collect(
    repo_root: Path = DEFAULT_REPO_ROOT,
    avail: ScannetAvailability | None = None,
) -> dict[str, list[MatchRecord]]:
    paths = _paths(repo_root)
    if avail is None:
        avail = ScannetAvailability(mode="disk", available_train=frozenset(), blocklist={})
    out: dict[str, list[MatchRecord]] = {}
    for name, it in DATASET_ITERATORS.items():
        if name in SCANNET_AWARE_ITERATORS:
            out[name] = list(it(paths, avail))
        else:
            out[name] = list(it(paths))
    return out


def write_outputs(
    grouped: dict[str, list[MatchRecord]],
    repo_root: Path = DEFAULT_REPO_ROOT,
    avail: ScannetAvailability | None = None,
) -> dict[str, Any]:
    paths = _paths(repo_root)
    out_dir = paths["out_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if avail is None:
        avail = ScannetAvailability(mode="disk", available_train=frozenset(), blocklist={})
    policy = avail.policy_label
    files: list[dict] = []
    for name, records in grouped.items():
        out_path = out_dir / f"{name}.json"
        payload: dict[str, Any] = {
            "schema_version": "1.0",
            "dataset": name,
            "generated_at": generated_at,
            "policy": policy,
            "count": len(records),
            "records": [asdict(r) for r in records],
        }
        if name in SCANNET_AWARE_ITERATORS and avail.blocklist:
            payload["scannet_blocklist_applied"] = {
                "count": len(avail.blocklist),
                "scenes": sorted(avail.blocklist.keys()),
            }
        with out_path.open("w") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        files.append(
            {
                "dataset": name,
                "path_repo": str(out_path.relative_to(repo_root)),
                "count": len(records),
            }
        )
    index = {
        "schema_version": "1.0",
        "generated_at": generated_at,
        "policy": policy,
        "scope": "tier1_3d_grounded",
        "files": files,
        "total": sum(f["count"] for f in files),
    }
    idx_path = out_dir / "index.json"
    with idx_path.open("w") as f:
        json.dump(index, f, indent=2)
    return index


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print counts only; do not write files")
    ap.add_argument(
        "--scannet-availability",
        choices=("disk", "manifest"),
        default="disk",
        help="how to determine ScanNet train scene presence",
    )
    ap.add_argument(
        "--scannet-availability-manifest",
        default=None,
        help="text file with one scene_id per line (required when --scannet-availability=manifest)",
    )
    ap.add_argument(
        "--scannet-blocklist",
        default=None,
        help="JSON file listing scenes with per-file missing info",
    )
    args = ap.parse_args()

    if args.scannet_availability == "manifest" and not args.scannet_availability_manifest:
        ap.error("--scannet-availability=manifest requires --scannet-availability-manifest PATH")

    avail = ScannetAvailability(
        mode=args.scannet_availability,
        available_train=_load_availability_manifest(args.scannet_availability_manifest),
        blocklist=_load_blocklist(args.scannet_blocklist),
    )

    grouped = collect(avail=avail)
    print(f"{'dataset':<22}{'records':>10}")
    print("-" * 32)
    total = 0
    for name, records in grouped.items():
        print(f"{name:<22}{len(records):>10}")
        total += len(records)
    print("-" * 32)
    print(f"{'TOTAL':<22}{total:>10}")
    if avail.blocklist:
        ann = sum(
            1
            for recs in grouped.values()
            for r in recs
            if "scannet_missing_files" in (r.extra or {})
        )
        print(f"blocklist scenes: {len(avail.blocklist)}  annotated records: {ann}")
    print(f"policy: {avail.policy_label}")

    if args.dry_run:
        return

    index = write_outputs(grouped, avail=avail)
    out_dir_rel = _paths(DEFAULT_REPO_ROOT)["out_dir"].relative_to(DEFAULT_REPO_ROOT)
    print(f"\nwrote {len(index['files'])} dataset files + index.json under {out_dir_rel}")


if __name__ == "__main__":
    main()
