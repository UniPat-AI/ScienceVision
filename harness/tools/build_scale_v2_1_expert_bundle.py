#!/usr/bin/env python3
"""Build the self-contained scale-v2.1 expert-review candidate bundle."""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

try:
    from tools import build_scale_v2_expert_bundle as base
except ImportError:  # Direct execution puts tools/ rather than the repo on sys.path.
    import build_scale_v2_expert_bundle as base


REPO_ROOT = Path(__file__).resolve().parents[1]
SCALE_DIR = REPO_ROOT / "QA-Gen/tasks/scale-v2.1"
STANDARD_QC_DIR = REPO_ROOT / "outputs/qa-qc/scale-v2.1-reviewed-20260810"
ROUND2_QC_DIR = REPO_ROOT / "outputs/qa-qc/scale-v2.1-round2-final-20260810"
LOCAL_PACKAGES_ROOT = Path(
    os.environ.get(
        "SCIENCEVISION_LOCAL_PACKAGES_DIR",
        REPO_ROOT.parent / "ScienceVision-local-packages",
    )
).expanduser()
DEFAULT_REFERENCE = LOCAL_PACKAGES_ROOT / "scale-v2-expert-review-candidate986-20260810"
DEFAULT_ASSET_SOURCE = LOCAL_PACKAGES_ROOT / "expert-v2-scale-v1-distinct-20260805"
DEFAULT_OUTPUT = LOCAL_PACKAGES_ROOT / "scale-v2.1-expert-review-candidate823-20260810"
BUNDLE_ID = "scale-v2.1-expert-review-candidate823-20260810"
GENERATED_AT = "2026-08-10T00:00:00Z"
REPAIR_REVISION = "2026-08-11-root-relative-asset-urls-and-ct-label-filter"
EXPECTED_CANDIDATE = 823
EXPECTED_QUARANTINE = 510
EXPECTED_TOTAL = 1333
EXPECTED_SOURCE = 1567
EXPECTED_ASSETS = 1307
EXPECTED_ASSET_BYTES = 5_581_980_948
TASK_SCREENSHOT_SCRIPT = '<script src="/viewer/shared/task-screenshot.js"></script>'
SCREENSHOT_API_SCRIPT = '<script src="/viewer/shared/screenshot-api.js"></script>'
SCREENSHOT_SHARED_FILES = ("screenshot-api.js", "task-screenshot.js")


def task_sources(disposition: str) -> list[Path]:
    stem = "candidate" if disposition == "candidate" else "quarantine"
    parent = SCALE_DIR if disposition == "candidate" else SCALE_DIR / "quarantine"
    return [
        parent / f"ScienceVision_scale-v2.1-{domain}_{stem}_tasks.jsonl"
        for domain in ("biology", "chemistry", "medical")
    ]


def load_tasks(
    disposition: str,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    origins: dict[str, dict[str, Any]] = {}
    for path in task_sources(disposition):
        for line_number, row in enumerate(base.read_jsonl(path), 1):
            task_id = row.get("task_id")
            if not isinstance(task_id, str) or not task_id or task_id in origins:
                raise ValueError(
                    f"{disposition} source has missing or duplicate task_id {task_id!r}"
                )
            origins[task_id] = {
                "source": base.repo_relative(path),
                "source_line": line_number,
            }
            rows.append(row)
    return rows, origins


def group_by(rows: Iterable[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        value = row.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"row has no {key}: {row}")
        result[value].append(row)
    return dict(result)


def verify_build_manifest() -> tuple[dict[str, Any], dict[str, Any]]:
    build_path = SCALE_DIR / "scale-v2.1-build.json"
    source_lock_path = SCALE_DIR / "source-lock.json"
    build = base.read_json(build_path)
    source_lock = base.read_json(source_lock_path)
    if build.get("source_lock_sha256") != base.sha256(source_lock_path):
        raise ValueError("scale-v2.1 source lock hash drift")
    for output in build.get("outputs", []):
        if not isinstance(output, dict) or not isinstance(output.get("path"), str):
            raise ValueError("invalid scale-v2.1 build output entry")
        path = SCALE_DIR / output["path"]
        if not path.is_file() or path.stat().st_size != output.get("byte_size"):
            raise FileNotFoundError(f"missing or size-drifted build output: {path}")
        if base.sha256(path) != output.get("sha256"):
            raise ValueError(f"scale-v2.1 build output hash drift: {path}")
    counts = build.get("counts", {})
    if counts.get("candidate") != EXPECTED_CANDIDATE:
        raise ValueError("scale-v2.1 candidate count drift")
    if counts.get("quarantine") != EXPECTED_QUARANTINE:
        raise ValueError("scale-v2.1 quarantine count drift")
    if counts.get("unique_assets") != EXPECTED_ASSETS:
        raise ValueError("scale-v2.1 asset count drift")
    if counts.get("asset_bytes") != EXPECTED_ASSET_BYTES:
        raise ValueError("scale-v2.1 asset byte count drift")
    return build, source_lock


def validate_collections(
    candidates: list[dict[str, Any]],
    quarantined: list[dict[str, Any]],
    ledger: list[dict[str, Any]],
    receipts: list[dict[str, Any]],
    standard_qc: dict[str, dict[str, Any]],
    round2_qc: dict[str, dict[str, Any]],
    quarantine_audit: dict[str, dict[str, Any]],
) -> None:
    candidate_ids = set(base.keyed(candidates, "candidate tasks"))
    quarantine_ids = set(base.keyed(quarantined, "quarantine tasks"))
    if len(candidate_ids) != EXPECTED_CANDIDATE:
        raise ValueError(f"expected {EXPECTED_CANDIDATE} candidates")
    if len(quarantine_ids) != EXPECTED_QUARANTINE:
        raise ValueError(f"expected {EXPECTED_QUARANTINE} quarantine tasks")
    if candidate_ids & quarantine_ids:
        raise ValueError("candidate and quarantine task IDs overlap")
    output_ids = candidate_ids | quarantine_ids
    if len(output_ids) != EXPECTED_TOTAL:
        raise ValueError("scale-v2.1 output task count drift")
    if len(ledger) != EXPECTED_SOURCE:
        raise ValueError("scale-v2.1 source ledger count drift")
    source_ids = [row.get("source_task_id") for row in ledger]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("source ledger task IDs are not unique")
    ledger_output_ids = {row.get("output_task_id") for row in ledger}
    if ledger_output_ids != output_ids:
        raise ValueError("ledger does not cover the output partition")
    receipt_ids = [row.get("receipt_id") for row in receipts]
    if len(receipts) != EXPECTED_TOTAL or len(receipt_ids) != len(set(receipt_ids)):
        raise ValueError("oracle receipt count or identity drift")
    if {row.get("task_id") for row in receipts} != output_ids:
        raise ValueError("oracle receipts do not cover the output partition")
    if any(row.get("selection_receipt_id") not in set(receipt_ids) for row in ledger):
        raise ValueError("ledger references a missing oracle receipt")
    if set(standard_qc) != candidate_ids or set(round2_qc) != candidate_ids:
        raise ValueError("candidate QC is not a strict 1:1 join")
    if set(quarantine_audit) != quarantine_ids:
        raise ValueError("quarantine audit is not a strict 1:1 join")
    if any(row.get("decision") == "block" for row in round2_qc.values()):
        raise ValueError("final round2 still contains blocked candidates")
    if Counter(row.get("decision") for row in round2_qc.values()) != Counter(
        {"pass": 605, "review": 218}
    ):
        raise ValueError("final round2 decision distribution drift")
    if any(row.get("status") != "current" for row in quarantine_audit.values()):
        raise ValueError("quarantine reason audit contains drift")
    if Counter(row["domain"] for row in candidates) != Counter(
        {"biology": 113, "chemistry": 105, "medical": 605}
    ):
        raise ValueError("candidate domain distribution drift")


def inject_task_screenshot_runtime(text: str) -> str:
    if TASK_SCREENSHOT_SCRIPT in text:
        return text
    body_needle = "</body>"
    if text.count(body_needle) != 1:
        raise RuntimeError("reference task viewer has an unexpected body")
    return text.replace(body_needle, TASK_SCREENSHOT_SCRIPT + "\n" + body_needle)


def _replace_runtime_anchor(text: str, needle: str, replacement: str, label: str) -> str:
    if text.count(needle) != 1:
        raise RuntimeError(f"reference {label} has an unexpected runtime structure")
    return text.replace(needle, replacement, 1)


def inject_molstar_screenshot_runtime(text: str) -> str:
    markers = (
        SCREENSHOT_API_SCRIPT,
        "const screenshotProvider = window.__ScienceVisionScreenshotProvider;",
        "async function captureFullMolstarViewport(helper)",
        "screenshotProvider.ready(",
        "screenshotProvider.fail(",
    )
    present = [marker in text for marker in markers]
    if any(present):
        if not all(present):
            raise RuntimeError("reference Mol* viewer has a partial screenshot integration")
        return text

    capture_helper = """
  async function captureFullMolstarViewport(helper) {
    const previousCropParams = { ...helper.cropParams };
    const previousRelativeCrop = { ...helper.relativeCrop };
    try {
      helper.behaviors.cropParams.next({ ...previousCropParams, auto: false });
      helper.behaviors.relativeCrop.next({ x: 0, y: 0, width: 1, height: 1 });
      return await helper.getImageDataUri();
    } finally {
      helper.behaviors.relativeCrop.next(previousRelativeCrop);
      helper.behaviors.cropParams.next(previousCropParams);
    }
  }

"""
    provider = """
      screenshotProvider.ready(async () => {
        const helper = viewer.plugin.helpers?.viewportScreenshot;
        if (helper?.getImageDataUri && helper.behaviors?.cropParams?.next
            && helper.behaviors?.relativeCrop?.next) {
          return captureFullMolstarViewport(helper);
        }
        const canvas = document.getElementById('viewer').querySelector('canvas');
        if (!canvas || !canvas.width || !canvas.height) {
          throw new Error('Mol* render canvas is unavailable.');
        }
        return new Promise((resolve, reject) => {
          canvas.toBlob(blob => {
            if (blob) resolve(blob);
            else reject(new Error('Mol* could not encode its render canvas.'));
          }, 'image/png');
        });
      });
"""
    text = _replace_runtime_anchor(
        text,
        '<script src="/viewer/shared/capture/annotate-shell.js" type="module"></script>\n',
        '<script src="/viewer/shared/capture/annotate-shell.js" type="module"></script>\n'
        + SCREENSHOT_API_SCRIPT
        + "\n",
        "Mol* viewer head",
    )
    text = _replace_runtime_anchor(
        text,
        "  const params = new URLSearchParams(location.search);\n",
        "  const params = new URLSearchParams(location.search);\n"
        "  const screenshotProvider = window.__ScienceVisionScreenshotProvider;\n",
        "Mol* viewer parameters",
    )
    text = _replace_runtime_anchor(
        text,
        "  let files = [];\n",
        capture_helper + "  let files = [];\n",
        "Mol* multi-structure parser",
    )
    no_files = (
        "  if (!files.length) {\n"
        "    if (!filesJSON) document.getElementById('viewer').innerHTML = "
        "'<div class=\"error-box\">No ?file= or ?files= query param.</div>';\n"
        "  } else {\n"
    )
    text = _replace_runtime_anchor(
        text,
        no_files,
        no_files.replace(
            "  } else {\n",
            "    screenshotProvider.fail(new Error('No structure file was provided.'));\n"
            "  } else {\n",
        ),
        "Mol* empty input branch",
    )
    load_anchor = "        await viewer.loadStructureFromUrl(item.file, molFormat);\n      }\n"
    text = _replace_runtime_anchor(
        text,
        load_anchor,
        load_anchor + provider,
        "Mol* structure loading loop",
    )
    text = _replace_runtime_anchor(
        text,
        "      .catch(err => {\n        const errorBox = document.createElement('div');\n",
        "      .catch(err => {\n"
        "        screenshotProvider.fail(err);\n"
        "        const errorBox = document.createElement('div');\n",
        "Mol* load error handler",
    )
    return text


def inject_niivue_screenshot_runtime(text: str) -> str:
    markers = (
        SCREENSHOT_API_SCRIPT,
        "const screenshotProvider = window.__ScienceVisionScreenshotProvider;",
        "screenshotProvider.ready(",
        "screenshotProvider.fail(",
    )
    present = [marker in text for marker in markers]
    if any(present):
        if not all(present):
            raise RuntimeError("reference NiiVue viewer has a partial screenshot integration")
        return text

    text = _replace_runtime_anchor(
        text,
        '<script src="/viewer/shared/neg-banner.js" defer></script>\n',
        '<script src="/viewer/shared/neg-banner.js" defer></script>\n'
        + SCREENSHOT_API_SCRIPT
        + "\n",
        "NiiVue viewer head",
    )
    text = _replace_runtime_anchor(
        text,
        "  const overlay = params.get('overlay');\n",
        "  const overlay = params.get('overlay');\n"
        "  const screenshotProvider = window.__ScienceVisionScreenshotProvider;\n",
        "NiiVue viewer parameters",
    )
    text = _replace_runtime_anchor(
        text,
        "    document.body.insertAdjacentHTML('beforeend', "
        "'<div class=\"error-box\">No ?file= query param.</div>');\n",
        "    document.body.insertAdjacentHTML('beforeend', "
        "'<div class=\"error-box\">No ?file= query param.</div>');\n"
        "    screenshotProvider.fail(new Error('No volume file was provided.'));\n",
        "NiiVue empty input branch",
    )
    text = _replace_runtime_anchor(
        text,
        "  } else {\n"
        "    // niivue 0.45 SLICE_TYPE: AXIAL=0, CORONAL=1, SAGITTAL=2, "
        "MULTIPLANAR=3, RENDER=4\n",
        "  } else {\n"
        "    async function initializeViewer() {\n"
        "    // niivue 0.45 SLICE_TYPE: AXIAL=0, CORONAL=1, SAGITTAL=2, "
        "MULTIPLANAR=3, RENDER=4\n",
        "NiiVue initialization block",
    )
    text = _replace_runtime_anchor(
        text,
        "    nv.attachTo('gl');\n",
        "    await nv.attachTo('gl');\n",
        "NiiVue canvas attachment",
    )
    load_block = (
        "    nv.loadVolumes(volumes).catch(err => {\n"
        "      document.body.insertAdjacentHTML('beforeend',\n"
        "        '<div class=\"error-box\">Failed to load:\\n' + err + '</div>');\n"
        "    });\n"
    )
    text = _replace_runtime_anchor(
        text,
        load_block,
        "    await nv.loadVolumes(volumes);\n",
        "NiiVue volume loading",
    )
    tail = (
        "      hint.textContent = HINTS[mode] || '';\n"
        "    });\n"
        "  }\n"
        "</script>\n"
    )
    provider_tail = """      hint.textContent = HINTS[mode] || '';
    });

    screenshotProvider.ready(() => {
      const canvas = nv.canvas || document.getElementById('gl');
      if (!canvas || !canvas.width || !canvas.height) {
        throw new Error('NiiVue render canvas is unavailable.');
      }
      nv.drawScene();
      return new Promise((resolve, reject) => {
        canvas.toBlob(blob => {
          if (blob) resolve(blob);
          else reject(new Error('NiiVue could not encode its render canvas.'));
        }, 'image/png');
      });
    });
    }
    initializeViewer().catch(err => {
      screenshotProvider.fail(err);
      document.body.insertAdjacentHTML('beforeend',
        '<div class="error-box">Failed to load:\\n' + err + '</div>');
    });
  }
</script>
"""
    return _replace_runtime_anchor(
        text,
        tail,
        provider_tail,
        "NiiVue interaction block",
    )


def copy_runtime(stage: Path, reference: Path) -> None:
    base.copy_runtime(stage, reference)
    shared = stage / "viewer/shared"
    if not shared.is_dir():
        raise FileNotFoundError("reference runtime has no viewer/shared directory")
    for name in SCREENSHOT_SHARED_FILES:
        shutil.copy2(REPO_ROOT / "viewer/shared" / name, shared / name)

    molstar = stage / "viewer/molstar.html"
    molstar.write_text(
        inject_molstar_screenshot_runtime(molstar.read_text(encoding="utf-8")),
        encoding="utf-8",
    )
    niivue = stage / "viewer/niivue.html"
    niivue.write_text(
        inject_niivue_screenshot_runtime(niivue.read_text(encoding="utf-8")),
        encoding="utf-8",
    )

    task_viewer = stage / "viewer/sciencevision_tasks.html"
    text = task_viewer.read_text(encoding="utf-8")
    mask_needle = (
        "  if (viewer === 'niivue' && a.mask_path) "
        "params.set('overlay', a.mask_path);\n"
    )
    mask_patch = (
        "  if (viewer === 'niivue') {\n"
        "    const maskId = a.mask_id ?? task.mask_id ?? "
        "task.target_selection?.mask_id;\n"
        "    if (maskId != null) params.set('mask_id', String(maskId));\n"
        "  }\n"
    )
    if mask_patch not in text:
        if text.count(mask_needle) != 1:
            raise RuntimeError("reference task viewer has an unexpected NiiVue route")
        text = text.replace(mask_needle, mask_needle + mask_patch)
    text = text.replace("q.repaired_qc", "q.standard_qc")
    text = text.replace("Repaired QC", "Standard QC")
    answer_wrap_css = """
  .sv-task-answer .sv-task-kv {
    grid-template-columns: minmax(96px, max-content) minmax(0, 1fr);
  }
  .sv-task-answer .sv-task-kv code {
    display: block;
    min-width: 0;
    max-width: 100%;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    word-break: break-word;
  }
"""
    if ".sv-task-answer .sv-task-kv code {" not in text:
        style_needle = "</style>\n</head>"
        if text.count(style_needle) != 1:
            raise RuntimeError("reference task viewer has an unexpected style block")
        text = text.replace(style_needle, answer_wrap_css + style_needle)
    text = inject_task_screenshot_runtime(text)
    task_viewer.write_text(text, encoding="utf-8")
    base.sanitize_runtime_protocol_literals(stage)


def classification_summary(row: dict[str, Any] | None) -> dict[str, Any] | None:
    return base.classification_summary(row)


def build_status_rows(
    tasks: list[dict[str, Any]],
    disposition: str,
    standard_qc: dict[str, dict[str, Any]],
    round2_qc: dict[str, dict[str, Any]],
    quarantine_audit: dict[str, dict[str, Any]],
    ledger_by_output: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task in tasks:
        task_id = task["task_id"]
        standard = standard_qc.get(task_id)
        round2 = round2_qc.get(task_id)
        audit = quarantine_audit.get(task_id)
        evaluation_status = (
            (round2 or {}).get("evaluation_status")
            or (standard or {}).get("evaluation_status")
            or "missing"
        )
        ledger_rows = ledger_by_output[task_id]
        rows.append(
            {
                "schema_version": "sciencevision.expert_bundle.task_status.v2.1",
                "task_id": task_id,
                "domain": task["domain"],
                "family": task["knowledge"]["direction_id"],
                "disposition": disposition,
                "disposition_reason": task.get("disposition_reason"),
                "repair_status": (
                    "rebuilt_candidate" if disposition == "candidate" else "quarantined"
                ),
                "standard_qc": classification_summary(standard),
                "round2_qc": classification_summary(round2),
                "quarantine_audit": copy.deepcopy(audit),
                "source_task_count": len(ledger_rows),
                "source_task_ids": sorted(row["source_task_id"] for row in ledger_rows),
                "oracle_receipt_id": task.get("oracle_receipt_id"),
                "evaluation_status": evaluation_status,
                "formal_release": False,
            }
        )
    return rows


def bundle_rows(
    tasks: list[dict[str, Any]],
    disposition: str,
    origins: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for snapshot_line, task in enumerate(tasks, 1):
        row = copy.deepcopy(task)
        for asset in [row.get("asset"), *(row.get("assets") or [])]:
            if not isinstance(asset, dict):
                continue
            for field in ("path", "mask_path"):
                raw = asset.get(field)
                if raw in (None, ""):
                    continue
                if not isinstance(raw, str):
                    raise ValueError(
                        f"non-string bundle asset URL in {task['task_id']}: {field}"
                    )
                relative = PurePosixPath(raw.lstrip("/"))
                if (
                    not relative.parts
                    or relative.parts[0] not in base.ALLOWED_ASSET_ROOTS
                    or any(part in {"", ".", ".."} for part in relative.parts)
                ):
                    raise ValueError(
                        f"unsafe bundle asset URL in {task['task_id']}: {raw!r}"
                    )
                asset[field] = "/" + relative.as_posix()
        row["_bundle"] = {
            "bundle_id": BUNDLE_ID,
            "suite": f"scale-v2.1-{disposition}",
            "disposition": disposition,
            "formal_release": False,
            "qc_status": "/qc/task_status.jsonl",
            "snapshot_line": snapshot_line,
            **origins[task["task_id"]],
        }
        rows.append(row)
    return rows


def copy_snapshot(
    source: Path, destination: Path, stage: Path, *, sanitize: bool = False
) -> dict[str, Any]:
    return base.copy_snapshot(source, destination, stage, sanitize=sanitize)


def source_snapshots(stage: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    sources = [
        *task_sources("candidate"),
        *task_sources("quarantine"),
        SCALE_DIR / "README.md",
        SCALE_DIR / "generic_deterministic_rubric.schema.json",
        SCALE_DIR / "oracle_receipts.jsonl",
        SCALE_DIR / "scale-v2.1-build.json",
        SCALE_DIR / "source-lock.json",
        SCALE_DIR / "task_ledger.jsonl",
    ]
    for source in sources:
        relative = source.relative_to(SCALE_DIR)
        records.append(
            copy_snapshot(source, stage / "sources/scale-v2.1" / relative, stage)
        )
    extra_sources = (
        REPO_ROOT / "docs/qa-gen/SCALE_V1_REVIEWED_20260808_LOCK.json",
        REPO_ROOT / "docs/qa-gen/SCALE_V2_1_BUILD.md",
        REPO_ROOT
        / "QA-Gen/ScienceVisionTaskGenerator/schemas/sciencevision_task.schema.json",
    )
    for source in extra_sources:
        destination = (
            stage / "sources/schema" / source.name
            if source.name.endswith(".schema.json")
            else stage / "sources/locks" / source.name
        )
        records.append(copy_snapshot(source, destination, stage))
    for source_dir in (STANDARD_QC_DIR, ROUND2_QC_DIR):
        for source in sorted(path for path in source_dir.iterdir() if path.is_file()):
            destination = stage / "qc" / source_dir.name / source.name
            records.append(copy_snapshot(source, destination, stage, sanitize=True))
    return records


def write_readme(stage: Path) -> None:
    (stage / "README.md").write_text(
        """# ScienceVision scale-v2.1 expert-review bundle

This offline package contains 823 candidate tasks, 510 quarantined tasks, all
1,307 referenced assets, the complete scale-v2.1 ledger/oracle/lock snapshot,
and the final Mol*/NiiVue viewers required for review.

This is a candidate review artifact, not a formal release. All six baseline
tracks remain at 0/823, and every task is marked `formal_release: false`.
The default viewer opens the candidate set. Use the quarantine link in the
viewer to switch to the 510 quarantined tasks.

Run `python3 serve_bundle.py --verify-only` before review, then use START.sh,
START.command, START.cmd, or the native launcher. The server binds only to
127.0.0.1 and has no runtime network dependency.
""",
        encoding="utf-8",
    )


def write_index(
    stage: Path,
    candidates: list[dict[str, Any]],
    quarantined: list[dict[str, Any]],
    statuses: dict[str, dict[str, Any]],
) -> None:
    lines = [
        "# ScienceVision scale-v2.1 task index",
        "",
        "Formal release: **false**  ",
        f"Candidate tasks: **{len(candidates)}**  ",
        f"Quarantined tasks: **{len(quarantined)}**",
        "",
    ]
    for label, rows, manifest in (
        ("Candidate", candidates, "/bundle_manifest.json"),
        ("Quarantine", quarantined, "/bundle_manifest-quarantine.json"),
    ):
        lines.extend(
            [
                f"## {label}",
                "",
                "| # | Task ID | Domain | Family | Latest QC | Evaluation |",
                "|---:|---|---|---|---|---|",
            ]
        )
        for index, task in enumerate(rows, 1):
            status = statuses[task["task_id"]]
            latest = status.get("round2_qc") or status.get("standard_qc") or {}
            task_id = task["task_id"]
            link = (
                "viewer/sciencevision_tasks.html?manifest="
                f"{manifest.replace('/', '%2F')}&task={task_id}"
            )
            lines.append(
                f"| {index} | [{task_id}]({link}) | {task['domain']} | "
                f"{status['family']} | "
                f"{latest.get('decision') or latest.get('grade') or status['disposition']} | "
                f"{status['evaluation_status']} |"
            )
        lines.append("")
    (stage / "TASK_INDEX.md").write_text("\n".join(lines), encoding="utf-8")


def stable_hash(values: Iterable[str]) -> str:
    return base.stable_hash(values)


def build(args: argparse.Namespace) -> dict[str, Any]:
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {output}")
    if not args.reference.is_dir() or not args.asset_source.is_dir():
        raise FileNotFoundError("reference bundle or asset source is missing")

    build_manifest, source_lock = verify_build_manifest()
    candidates, candidate_origins = load_tasks("candidate")
    quarantined, quarantine_origins = load_tasks("quarantine")
    schema_path = (
        REPO_ROOT
        / "QA-Gen/ScienceVisionTaskGenerator/schemas/sciencevision_task.schema.json"
    )
    base.validate_schema(candidates + quarantined, schema_path)

    ledger = base.read_jsonl(SCALE_DIR / "task_ledger.jsonl")
    receipts = base.read_jsonl(SCALE_DIR / "oracle_receipts.jsonl")
    standard_rows = base.read_jsonl(STANDARD_QC_DIR / "task_classifications.jsonl")
    round2_rows = base.read_jsonl(ROUND2_QC_DIR / "task_classifications.jsonl")
    quarantine_rows = base.read_jsonl(ROUND2_QC_DIR / "quarantine_audit.jsonl")
    standard_qc = base.keyed(standard_rows, "standard QC")
    round2_qc = base.keyed(round2_rows, "final round2 QC")
    quarantine_audit = base.keyed(quarantine_rows, "quarantine audit")
    validate_collections(
        candidates,
        quarantined,
        ledger,
        receipts,
        standard_qc,
        round2_qc,
        quarantine_audit,
    )
    ledger_by_output = group_by(ledger, "output_task_id")
    asset_paths = base.safe_asset_paths(candidates + quarantined)
    if len(asset_paths) != EXPECTED_ASSETS:
        raise ValueError("asset closure count drift")

    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent))
    try:
        copy_runtime(stage, args.reference)
        asset_bytes = base.copy_assets(stage, args.asset_source, asset_paths)
        if asset_bytes != EXPECTED_ASSET_BYTES:
            raise ValueError(f"asset closure byte drift: {asset_bytes}")

        candidate_bundle_rows = bundle_rows(candidates, "candidate", candidate_origins)
        quarantine_bundle_rows = bundle_rows(
            quarantined, "quarantine", quarantine_origins
        )
        base.write_jsonl(stage / "tasks/candidate_tasks.jsonl", candidate_bundle_rows)
        base.write_jsonl(stage / "tasks/quarantine_tasks.jsonl", quarantine_bundle_rows)

        status_rows = build_status_rows(
            candidates,
            "candidate",
            standard_qc,
            round2_qc,
            quarantine_audit,
            ledger_by_output,
        ) + build_status_rows(
            quarantined,
            "quarantine",
            standard_qc,
            round2_qc,
            quarantine_audit,
            ledger_by_output,
        )
        base.write_jsonl(stage / "qc/task_status.jsonl", status_rows)
        statuses = base.keyed(status_rows, "bundle QC status")

        candidate_manifest = {
            "kind": "sciencevision-task-bundle",
            "schema_version": "sciencevision.task_bundle.v2",
            "bundle_id": BUNDLE_ID,
            "title": "scale-v2.1 expert-review candidates",
            "disposition": "candidate",
            "formal_release": False,
            "baseline_coverage": "0/823 for all six required tracks",
            "tasks_jsonl": "/tasks/candidate_tasks.jsonl",
            "task_count": len(candidates),
            "task_index": "/TASK_INDEX.md",
            "qc_status_jsonl": "/qc/task_status.jsonl",
            "related_manifests": [
                {
                    "label": "quarantine (510)",
                    "disposition": "quarantine",
                    "path": "/bundle_manifest-quarantine.json",
                }
            ],
        }
        quarantine_manifest = {
            **candidate_manifest,
            "title": "scale-v2.1 expert-review quarantine",
            "disposition": "quarantine",
            "tasks_jsonl": "/tasks/quarantine_tasks.jsonl",
            "task_count": len(quarantined),
            "related_manifests": [
                {
                    "label": "candidates (823)",
                    "disposition": "candidate",
                    "path": "/bundle_manifest.json",
                }
            ],
        }
        base.write_json(stage / "bundle_manifest.json", candidate_manifest)
        base.write_json(stage / "bundle_manifest-quarantine.json", quarantine_manifest)

        snapshot_records = source_snapshots(stage)
        write_readme(stage)
        write_index(stage, candidates, quarantined, statuses)
        candidate_ids = [row["task_id"] for row in candidates]
        quarantine_ids = [row["task_id"] for row in quarantined]
        all_ids = candidate_ids + quarantine_ids
        base.write_json(
            stage / "SELECTION.json",
            {
                "schema_version": "sciencevision.scale_v2_1.selection.v1",
                "candidate_count": len(candidate_ids),
                "quarantine_count": len(quarantine_ids),
                "total_count": len(all_ids),
                "source_ledger_count": len(ledger),
                "partition": "strict_disjoint_complete",
                "candidate_task_ids_sha256": stable_hash(candidate_ids),
                "quarantine_task_ids_sha256": stable_hash(quarantine_ids),
                "all_task_ids_sha256": stable_hash(all_ids),
                "asset_count": len(asset_paths),
                "asset_paths_sha256": stable_hash(
                    path.as_posix() for path in asset_paths
                ),
                "formal_release": False,
            },
        )
        git_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        base.write_json(
            stage / "BUNDLE_METADATA.json",
            {
                "schema_version": "sciencevision.expert_bundle.metadata.v2.1",
                "bundle_id": BUNDLE_ID,
                "generated_at": GENERATED_AT,
                "source_dataset": "scale-v1",
                "output_dataset": "scale-v2.1",
                "git_commit": git_commit,
                "task_counts": {
                    "candidate": len(candidates),
                    "quarantine": len(quarantined),
                    "output_total": len(candidates) + len(quarantined),
                    "source_ledger": len(ledger),
                },
                "asset_count": len(asset_paths),
                "asset_bytes": asset_bytes,
                "offline": True,
                "zero_symlinks": True,
                "repair_revision": REPAIR_REVISION,
                "baseline_coverage": {
                    name: {"observed": 0, "expected": EXPECTED_CANDIDATE}
                    for name in ("cc", "ccmcp", "cc2d-1", "cc2d-3", "cc2d-6", "cc2d-12")
                },
                "runtime": {
                    "native_macos": "macOS 12 or later",
                    "native_windows": "bundled launcher",
                    "fallback": "Python 3",
                    "bind": "127.0.0.1",
                },
                "formal_release": False,
            },
        )
        base.write_json(
            stage / "MANIFEST.json",
            {
                "schema_version": "sciencevision.expert_bundle.manifest.v2.1",
                "bundle_id": BUNDLE_ID,
                "generated_at": GENERATED_AT,
                "git_commit": git_commit,
                "formal_release": False,
                "source_build": {
                    "path": "QA-Gen/tasks/scale-v2.1/scale-v2.1-build.json",
                    "sha256": base.sha256(SCALE_DIR / "scale-v2.1-build.json"),
                    "build_id": build_manifest["build_id"],
                    "schema_version": build_manifest.get("schema_version"),
                    "source_lock_path": "QA-Gen/tasks/scale-v2.1/source-lock.json",
                    "source_lock_sha256": base.sha256(SCALE_DIR / "source-lock.json"),
                    "source_lock_schema_version": source_lock.get("schema_version"),
                },
                "strict_schema": {
                    "path": base.repo_relative(schema_path),
                    "sha256": base.sha256(schema_path),
                    "validated_task_count": len(candidates) + len(quarantined),
                },
                "joins": {
                    "source_ledger_count": len(ledger),
                    "oracle_receipt_count": len(receipts),
                    "standard_qc_count": len(standard_rows),
                    "round2_qc_count": len(round2_rows),
                    "quarantine_audit_count": len(quarantine_rows),
                    "status_count": len(status_rows),
                    "status": "strict_complete",
                },
                "round2": {
                    "decisions": {"pass": 605, "review": 218, "block": 0},
                    "materialization_finding_count": 0,
                },
                "assets": {
                    "count": len(asset_paths),
                    "bytes": asset_bytes,
                    "paths_sha256": stable_hash(
                        path.as_posix() for path in asset_paths
                    ),
                },
                "viewer": {
                    "source": "offline bundle shell plus current Mol*/NiiVue runtime",
                    "repair_revision": REPAIR_REVISION,
                    "asset_url_projection": "root_relative",
                    "ct_mask_policy": "mask voxel value equals mask_id",
                    "ct_mask_rendering": "in_memory_binary_selected_label_only",
                    "niivue_coordinate_readout": True,
                    "molstar_detailed_atom_identity": True,
                },
                "source_snapshots": snapshot_records,
            },
        )

        base.validate_stage(stage, asset_paths)
        file_count, payload_bytes = base.write_checksums(stage)
        checksum_hash = base.sha256(stage / "SHA256SUMS")
        os.replace(stage, output)
        return {
            "output": str(output),
            "candidate_tasks": len(candidates),
            "quarantine_tasks": len(quarantined),
            "source_ledger": len(ledger),
            "assets": len(asset_paths),
            "asset_bytes": asset_bytes,
            "checksummed_files": file_count,
            "payload_bytes": payload_bytes,
            "sha256sums_sha256": checksum_hash,
        }
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--asset-source", type=Path, default=DEFAULT_ASSET_SOURCE)
    return parser.parse_args()


def main() -> int:
    result = build(parse_args())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
