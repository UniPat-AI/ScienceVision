#!/usr/bin/env python3
"""Build the self-contained scale-v2 expert-review candidate bundle."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_PACKAGES_ROOT = Path(
    os.environ.get(
        "SCIENCEVISION_LOCAL_PACKAGES_DIR",
        REPO_ROOT.parent / "ScienceVision-local-packages",
    )
).expanduser()
DEFAULT_REFERENCE = LOCAL_PACKAGES_ROOT / "expert-v2-scale-v1-sample165-20260805"
DEFAULT_ASSET_SOURCE = LOCAL_PACKAGES_ROOT / "expert-v2-scale-v1-distinct-20260805"
DEFAULT_OUTPUT = LOCAL_PACKAGES_ROOT / "scale-v2-expert-review-candidate986-20260810"
BUNDLE_ID = "scale-v2-expert-review-candidate986-20260810"
GENERATED_AT = "2026-08-10T00:00:00Z"
EXPECTED_CANDIDATE = 986
EXPECTED_QUARANTINE = 581
EXPECTED_TOTAL = 1567
EXPECTED_ASSETS = 1307
ALLOWED_ASSET_ROOTS = {"crawl_data", "datasets"}
TEXT_SUFFIXES = {
    ".cmd",
    ".command",
    ".css",
    ".html",
    ".js",
    ".json",
    ".jsonl",
    ".md",
    ".plist",
    ".py",
    ".sh",
    ".txt",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(values: Iterable[str]) -> str:
    payload = "".join(f"{value}\n" for value in values).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def repo_relative(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError(f"expected JSON object at {path}:{line_number}")
        rows.append(value)
    return rows


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(
                json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                + "\n"
            )


def keyed(rows: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        task_id = row.get("task_id")
        if not isinstance(task_id, str) or not task_id or task_id in result:
            raise ValueError(f"{label} has a missing or duplicate task_id: {task_id!r}")
        result[task_id] = row
    return result


def validate_output(output: Path) -> None:
    if output.exists():
        if not output.is_dir() or any(output.iterdir()):
            raise FileExistsError(f"refusing to replace existing non-empty output: {output}")


def json_type_matches(value: Any, expected: str) -> bool:
    checks = {
        "null": lambda item: item is None,
        "boolean": lambda item: isinstance(item, bool),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "string": lambda item: isinstance(item, str),
        "array": lambda item: isinstance(item, list),
        "object": lambda item: isinstance(item, dict),
    }
    return checks[expected](value)


def resolve_ref(root: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"unsupported non-local schema reference: {ref}")
    value: Any = root
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or part not in value:
            raise ValueError(f"unresolved schema reference: {ref}")
        value = value[part]
    if not isinstance(value, dict):
        raise ValueError(f"schema reference is not an object: {ref}")
    return value


def schema_errors(
    value: Any,
    schema: dict[str, Any],
    root: dict[str, Any],
    location: str = "<root>",
) -> list[str]:
    if "$ref" in schema:
        return schema_errors(value, resolve_ref(root, schema["$ref"]), root, location)

    expected = schema.get("type")
    if expected is not None:
        expected_types = [expected] if isinstance(expected, str) else expected
        if not isinstance(expected_types, list) or not all(
            isinstance(item, str) for item in expected_types
        ):
            raise ValueError(f"invalid type declaration in schema at {location}")
        if not any(json_type_matches(value, item) for item in expected_types):
            return [f"{location}: expected type {expected_types}, got {type(value).__name__}"]

    if "const" in schema and value != schema["const"]:
        return [f"{location}: expected constant {schema['const']!r}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{location}: value {value!r} is outside the allowed enum"]

    errors: list[str] = []
    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{location}: missing required property {key!r}")
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for key, child_schema in properties.items():
                if key in value and isinstance(child_schema, dict):
                    errors.extend(
                        schema_errors(value[key], child_schema, root, f"{location}.{key}")
                    )
            additional = schema.get("additionalProperties", True)
            for key in value.keys() - properties.keys():
                if additional is False:
                    errors.append(f"{location}: unexpected property {key!r}")
                elif isinstance(additional, dict):
                    errors.extend(
                        schema_errors(value[key], additional, root, f"{location}.{key}")
                    )
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            errors.append(f"{location}: fewer than {schema['minProperties']} properties")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{location}: fewer than {schema['minItems']} items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(schema_errors(item, item_schema, root, f"{location}[{index}]"))
        if schema.get("uniqueItems"):
            serialized = [
                json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                for item in value
            ]
            if len(serialized) != len(set(serialized)):
                errors.append(f"{location}: array items are not unique")
        contains = schema.get("contains")
        if isinstance(contains, dict) and not any(
            not schema_errors(item, contains, root, f"{location}[{index}]")
            for index, item in enumerate(value)
        ):
            errors.append(f"{location}: no item satisfies contains")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{location}: string is shorter than {schema['minLength']}")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            errors.append(f"{location}: string does not match {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{location}: value is below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{location}: value is above {schema['maximum']}")

    any_of = schema.get("anyOf")
    if isinstance(any_of, list) and not any(
        isinstance(option, dict) and not schema_errors(value, option, root, location)
        for option in any_of
    ):
        errors.append(f"{location}: no anyOf branch matched")
    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for option in all_of:
            if isinstance(option, dict):
                errors.extend(schema_errors(value, option, root, location))
    condition = schema.get("if")
    if isinstance(condition, dict):
        branch = schema.get("then") if not schema_errors(value, condition, root, location) else schema.get("else")
        if isinstance(branch, dict):
            errors.extend(schema_errors(value, branch, root, location))
    return errors


def validate_schema(rows: list[dict[str, Any]], schema_path: Path) -> None:
    schema = read_json(schema_path)
    for row in rows:
        errors = schema_errors(row, schema, schema)
        if errors:
            raise ValueError(
                f"schema validation failed for {row.get('task_id')!r}: {errors[0]}"
            )


def task_sources(disposition: str) -> list[Path]:
    base = REPO_ROOT / "QA-Gen/tasks/scale-v2"
    if disposition == "candidate":
        return [
            base / f"ScienceVision_scale-v2-{domain}_candidate_tasks.jsonl"
            for domain in ("biology", "chemistry", "medical")
        ]
    return [
        base / "quarantine" / f"ScienceVision_scale-v2-{domain}_quarantine_tasks.jsonl"
        for domain in ("biology", "chemistry", "medical")
    ]


def load_tasks(disposition: str) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    origins: dict[str, dict[str, Any]] = {}
    for path in task_sources(disposition):
        for line_number, row in enumerate(read_jsonl(path), 1):
            task_id = row.get("task_id")
            if not isinstance(task_id, str) or not task_id or task_id in origins:
                raise ValueError(
                    f"{disposition} source has missing or duplicate task_id {task_id!r}"
                )
            origins[task_id] = {
                "source": repo_relative(path),
                "source_line": line_number,
            }
            rows.append(row)
    return rows, origins


def verify_build_manifest(scale_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    build_path = scale_dir / "scale-v2-build.json"
    lock_path = REPO_ROOT / "docs/qa-gen/SCALE_V2_BUILD_20260808_LOCK.json"
    build = read_json(build_path)
    lock = read_json(lock_path)
    if lock.get("build_manifest_sha256") != sha256(build_path):
        raise ValueError("scale-v2 build manifest does not match its independent lock")
    if lock.get("build_manifest_path") != repo_relative(build_path):
        raise ValueError("scale-v2 build lock points at an unexpected manifest")
    for output in build.get("outputs", []):
        if not isinstance(output, dict) or not isinstance(output.get("path"), str):
            raise ValueError("invalid scale-v2 build output entry")
        path = scale_dir / output["path"]
        if not path.is_file():
            raise FileNotFoundError(path)
        if sha256(path) != output.get("sha256"):
            raise ValueError(f"scale-v2 build output hash drift: {path}")
        if len(read_jsonl(path)) != output.get("task_count"):
            raise ValueError(f"scale-v2 build output count drift: {path}")
    return build, lock


def load_decisions(path: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    rows = read_jsonl(path)
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        source = row.get("source")
        task_id = source.get("task_id") if isinstance(source, dict) else None
        if not isinstance(task_id, str) or not task_id or task_id in result:
            raise ValueError(f"decision ledger has missing or duplicate task_id: {task_id!r}")
        result[task_id] = row
    return rows, result


def validate_collections(
    candidates: list[dict[str, Any]],
    quarantined: list[dict[str, Any]],
    decisions: dict[str, dict[str, Any]],
    repaired: dict[str, dict[str, Any]],
    round2: dict[str, dict[str, Any]],
    quarantine_audit: dict[str, dict[str, Any]],
) -> None:
    candidate_ids = set(keyed(candidates, "candidate tasks"))
    quarantine_ids = set(keyed(quarantined, "quarantine tasks"))
    if len(candidate_ids) != EXPECTED_CANDIDATE:
        raise ValueError(f"expected {EXPECTED_CANDIDATE} candidates, got {len(candidate_ids)}")
    if len(quarantine_ids) != EXPECTED_QUARANTINE:
        raise ValueError(
            f"expected {EXPECTED_QUARANTINE} quarantine tasks, got {len(quarantine_ids)}"
        )
    if candidate_ids & quarantine_ids:
        raise ValueError("candidate and quarantine task IDs overlap")
    all_ids = candidate_ids | quarantine_ids
    if len(all_ids) != EXPECTED_TOTAL or set(decisions) != all_ids:
        raise ValueError("decision ledger is not a strict join over all scale-v2 tasks")
    if set(repaired) != candidate_ids:
        raise ValueError("repaired QC is not a strict join over candidate tasks")
    if set(round2) != candidate_ids:
        raise ValueError("round2 QC is not a strict join over candidate tasks")
    if set(quarantine_audit) != quarantine_ids:
        raise ValueError("quarantine audit is not a strict join over quarantine tasks")
    for task_id in candidate_ids:
        if decisions[task_id].get("disposition") != "candidate":
            raise ValueError(f"candidate disposition mismatch: {task_id}")
    for task_id in quarantine_ids:
        if decisions[task_id].get("disposition") != "quarantine":
            raise ValueError(f"quarantine disposition mismatch: {task_id}")
    domain_counts = Counter(row["domain"] for row in candidates)
    if domain_counts != Counter({"medical": 839, "biology": 147}):
        raise ValueError(f"unexpected candidate domain counts: {dict(domain_counts)}")
    quarantine_counts = Counter(row["domain"] for row in quarantined)
    if quarantine_counts != Counter({"chemistry": 401, "biology": 180}):
        raise ValueError(f"unexpected quarantine domain counts: {dict(quarantine_counts)}")


def safe_asset_paths(tasks: list[dict[str, Any]]) -> list[PurePosixPath]:
    paths: set[PurePosixPath] = set()
    for task in tasks:
        objects: list[dict[str, Any]] = []
        asset = task.get("asset")
        if isinstance(asset, dict):
            objects.append(asset)
        assets = task.get("assets")
        if isinstance(assets, list):
            objects.extend(item for item in assets if isinstance(item, dict))
        for item in objects:
            for field in ("repo_path", "mask_repo_path"):
                raw = item.get(field)
                if raw in (None, ""):
                    continue
                if not isinstance(raw, str):
                    raise ValueError(f"non-string asset path in {task.get('task_id')}: {field}")
                path = PurePosixPath(raw)
                if (
                    path.is_absolute()
                    or not path.parts
                    or path.parts[0] not in ALLOWED_ASSET_ROOTS
                    or any(part in {"", ".", ".."} for part in path.parts)
                ):
                    raise ValueError(f"unsafe asset path in {task.get('task_id')}: {raw!r}")
                paths.add(path)
    result = sorted(paths, key=lambda item: item.as_posix())
    if len(result) != EXPECTED_ASSETS:
        raise ValueError(f"expected {EXPECTED_ASSETS} unique assets, got {len(result)}")
    return result


def copy_assets(stage: Path, asset_source: Path, paths: list[PurePosixPath]) -> int:
    source_root = asset_source.resolve()
    total_bytes = 0
    for relative in paths:
        source = asset_source.joinpath(*relative.parts)
        resolved = source.resolve()
        if not resolved.is_relative_to(source_root) or source.is_symlink() or not source.is_file():
            raise FileNotFoundError(f"missing, linked, or escaped asset: {relative.as_posix()}")
        destination = stage.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        total_bytes += destination.stat().st_size
    return total_bytes


def copy_runtime(stage: Path, reference: Path) -> None:
    required = [
        "index.html",
        "serve_bundle.py",
        "START.sh",
        "START.command",
        "START.cmd",
        "viewer",
        "打开 ScienceVision（Mac）.app",
        "打开 ScienceVision（Windows）.exe",
    ]
    for name in required:
        source = reference / name
        if not source.exists():
            raise FileNotFoundError(source)
        if any(item.is_symlink() for item in ([source] if source.is_file() else source.rglob("*"))):
            raise ValueError(f"runtime source contains a symlink: {source}")
        destination = stage / name
        if source.is_dir():
            shutil.copytree(
                source,
                destination,
                copy_function=shutil.copy2,
                ignore=shutil.ignore_patterns(".DS_Store"),
            )
        else:
            shutil.copy2(source, destination)


def transform_viewer(stage: Path) -> None:
    path = stage / "viewer/sciencevision_tasks.html"
    text = path.read_text(encoding="utf-8")

    replacements = [
        (
            "  .sv-task-detail {\n",
            """  .sv-qc-chip[data-tone="good"] { color: #3fb950; border-color: rgba(63,185,80,.55); }
  .sv-qc-chip[data-tone="warn"] { color: #d29922; border-color: rgba(210,153,34,.55); }
  .sv-qc-chip[data-tone="bad"] { color: #f85149; border-color: rgba(248,81,73,.55); }
  .sv-qc-panel { border-left: 3px solid #d29922; }
  .sv-qc-grid { display: grid; grid-template-columns: repeat(auto-fit,minmax(150px,1fr)); gap: 8px; margin-top: 8px; }
  .sv-qc-cell { padding: 8px; border: 1px solid var(--border); border-radius: 6px; background: var(--bg); }
  .sv-qc-label { color: var(--fg-dim); font-size: 10px; text-transform: uppercase; }
  .sv-qc-value { margin-top: 3px; font: 11px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace; overflow-wrap: anywhere; }
  .sv-qc-reasons { margin: 8px 0 0; padding-left: 18px; }
  .sv-task-detail {
""",
        ),
        (
            "  en: null,                // {task_id -> {title?, instruction?}} from <set>.en.json\n",
            "  en: null,                // {task_id -> {title?, instruction?}} from <set>.en.json\n"
            "  qc: {},                  // {task_id -> QC status} from bundle manifest\n",
        ),
        (
            "        ${verifyCardFlags(task.task_id)}\n",
            "        ${verifyCardFlags(task.task_id)}\n"
            "        ${qcCardChips(task)}\n",
        ),
        (
            "function renderPanel(task) {\n",
            """function qcTone(value) {
  if (['pass', 'qualified', 'candidate', 'complete', 'current'].includes(value)) return 'good';
  if (['block', 'unacceptable', 'quarantine', 'blocked'].includes(value)) return 'bad';
  return 'warn';
}

function qcChip(label, value) {
  if (value == null || value === '') return '';
  return `<span class="sv-task-chip sv-qc-chip" data-tone="${qcTone(value)}">${esc(label)}: ${esc(value)}</span>`;
}

function qcCardChips(task) {
  const q = state.qc[task.task_id];
  if (!q) return '';
  return [
    qcChip('set', q.disposition),
    qcChip('repair', q.repair_status),
    qcChip('QC2', q.round2_qc?.decision || q.repaired_qc?.decision),
    qcChip('eval', q.evaluation_status),
  ].join('');
}

function renderQC(task) {
  const q = state.qc[task.task_id];
  if (!q) return `<div class="sv-task-mini sv-qc-panel"><h3>QC Status</h3><span class="sv-task-status">missing QC join</span></div>`;
  const active = q.round2_qc || q.repaired_qc || q.source_qc || {};
  const reasons = Array.isArray(active.reasons) ? active.reasons : [];
  const reasonHtml = reasons.length
    ? `<ul class="sv-qc-reasons">${reasons.map(r => `<li><code>${esc(r.code || r)}</code>${r.message ? ` ${esc(r.message)}` : ''}</li>`).join('')}</ul>`
    : '<span class="sv-task-status">no summarized reason records</span>';
  return `<div class="sv-task-mini sv-qc-panel">
    <h3>QC Status ${qcChip('disposition', q.disposition)} ${qcChip('formal release', q.formal_release ? 'yes' : 'no')}</h3>
    <div class="sv-qc-grid">
      <div class="sv-qc-cell"><div class="sv-qc-label">Repair</div><div class="sv-qc-value">${esc(q.repair_status)}<br>${esc((q.repair_codes || []).join(', ') || 'none')}</div></div>
      <div class="sv-qc-cell"><div class="sv-qc-label">Source QC</div><div class="sv-qc-value">${esc(q.source_qc?.grade || 'n/a')} / ${esc(q.source_qc?.evaluation_status || 'n/a')}</div></div>
      <div class="sv-qc-cell"><div class="sv-qc-label">Repaired QC</div><div class="sv-qc-value">${esc(q.repaired_qc?.decision || 'n/a')} / ${esc(q.repaired_qc?.grade || 'n/a')}</div></div>
      <div class="sv-qc-cell"><div class="sv-qc-label">Round 2</div><div class="sv-qc-value">${esc(q.round2_qc?.decision || 'n/a')} / ${esc(q.round2_qc?.grade || 'n/a')}<br>${esc(q.round2_qc?.materialization_status || '')}</div></div>
      <div class="sv-qc-cell"><div class="sv-qc-label">Independent Evaluation</div><div class="sv-qc-value">${esc(q.evaluation_status)}</div></div>
    </div>
    ${reasonHtml}
  </div>`;
}

function renderPanel(task) {
""",
        ),
        (
            "        ${reviewHtml}\n",
            "        ${reviewHtml}\n"
            "        ${renderQC(task)}\n",
        ),
        (
            "  state.verify = null; state.verifyMeta = null; state.verifyTried = false;\n",
            "  state.verify = null; state.verifyMeta = null; state.verifyTried = false;\n"
            "  state.qc = {};\n",
        ),
        (
            "      els.links.innerHTML = `${indexLink}<a href=\"${esc(path)}\" target=\"_blank\" rel=\"noopener\">jsonl</a><a href=\"${esc(opts.manifest)}\" target=\"_blank\" rel=\"noopener\">manifest</a>`;\n",
            """      const relatedLinks = Array.isArray(bundle.related_manifests)
        ? bundle.related_manifests.map(item => `<a href="/viewer/sciencevision_tasks.html?manifest=${encodeURIComponent(item.path)}">${esc(item.label || item.disposition || 'related set')}</a>`).join('')
        : '';
      els.links.innerHTML = `${indexLink}<a href="${esc(path)}" target="_blank" rel="noopener">jsonl</a><a href="${esc(opts.manifest)}" target="_blank" rel="noopener">manifest</a>${relatedLinks}`;
""",
        ),
        (
            "    state.rows = parseJSONL(await resp.text());\n",
            """    state.rows = parseJSONL(await resp.text());
    if (state.bundle?.qc_status_jsonl) {
      const qcResp = await fetch(state.bundle.qc_status_jsonl);
      if (!qcResp.ok) throw new Error(`HTTP ${qcResp.status} while loading ${state.bundle.qc_status_jsonl}`);
      const qcRows = parseJSONL(await qcResp.text());
      state.qc = Object.fromEntries(qcRows.map(row => [row.task_id, row]));
      const missingQC = state.rows.filter(row => !state.qc[row.task_id]);
      if (missingQC.length) throw new Error(`${missingQC.length} tasks lack QC status records`);
    }
""",
        ),
        (
            "    const releaseReady = state.rows.filter(r => r.validation_record?.release_gate === 'pass').length;\n",
            "    const releaseReady = state.bundle?.formal_release === false ? 0 : state.rows.filter(r => r.validation_record?.release_gate === 'pass').length;\n",
        ),
    ]
    for old, new in replacements:
        count = text.count(old)
        if count != 1:
            raise RuntimeError(f"viewer transform expected one match, got {count}: {old[:80]!r}")
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")

    molstar_path = stage / "viewer/molstar.html"
    molstar_text = molstar_path.read_text(encoding="utf-8")
    molstar_needle = "      pdbProvider: 'rcsb',\n"
    if molstar_text.count(molstar_needle) != 1:
        raise RuntimeError("reference Mol* viewer has an unexpected initialization block")
    molstar_path.write_text(
        molstar_text.replace(
            molstar_needle,
            "      pdbProvider: 'rcsb',\n"
            "      layoutShowRemoteState: false,\n"
            "      // Volseg fetches a remote entry catalog during registration.\n"
            "      disabledExtensions: ['volseg'],\n",
        ),
        encoding="utf-8",
    )


def sanitize_runtime_protocol_literals(stage: Path) -> None:
    for path in (stage / "viewer").rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        if "file://" not in text:
            continue
        # Preserve the runtime string while preventing packaged local-file URLs.
        path.write_text(text.replace("file://", "file:\\u002f\\u002f"), encoding="utf-8")


def classification_summary(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    keys = (
        "decision",
        "grade",
        "grade_scope",
        "evaluation_status",
        "labels",
        "reasons",
        "recommended_actions",
        "qualification_status",
        "quality_gate_status",
        "release_status",
        "materialization_scope",
        "materialization_status",
        "render_status",
    )
    return {key: copy.deepcopy(row[key]) for key in keys if key in row}


def source_qc_summary(row: dict[str, Any]) -> dict[str, Any]:
    qc = row.get("qc") if isinstance(row.get("qc"), dict) else {}
    return {
        key: copy.deepcopy(qc[key])
        for key in ("grade", "grade_scope", "evaluation_status", "labels", "reason_codes")
        if key in qc
    }


def build_status_rows(
    tasks: list[dict[str, Any]],
    disposition: str,
    decisions: dict[str, dict[str, Any]],
    repaired: dict[str, dict[str, Any]],
    round2: dict[str, dict[str, Any]],
    quarantine_audit: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for task in tasks:
        task_id = task["task_id"]
        decision = decisions[task_id]
        repaired_row = repaired.get(task_id)
        round2_row = round2.get(task_id)
        audit_row = quarantine_audit.get(task_id)
        evaluation_status = (
            (round2_row or {}).get("evaluation_status")
            or (repaired_row or {}).get("evaluation_status")
            or (decision.get("qc") or {}).get("evaluation_status")
            or "missing"
        )
        result.append(
            {
                "schema_version": "sciencevision.expert_bundle.task_status.v1",
                "task_id": task_id,
                "domain": task["domain"],
                "family": decision.get("family"),
                "disposition": disposition,
                "disposition_reasons": decision.get("disposition_reasons", []),
                "repair_status": decision.get("status"),
                "repair_codes": decision.get("repair_codes", []),
                "repaired_qc_findings": decision.get("repaired_qc_findings", []),
                "source_qc": source_qc_summary(decision),
                "repaired_qc": classification_summary(repaired_row),
                "round2_qc": classification_summary(round2_row),
                "quarantine_audit": copy.deepcopy(audit_row),
                "evaluation_status": evaluation_status,
                "formal_release": False,
            }
        )
    return result


def bundle_rows(
    tasks: list[dict[str, Any]],
    disposition: str,
    origins: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for snapshot_line, task in enumerate(tasks, 1):
        row = copy.deepcopy(task)
        row["_bundle"] = {
            "bundle_id": BUNDLE_ID,
            "suite": f"scale-v2-{disposition}",
            "disposition": disposition,
            "formal_release": False,
            "qc_status": "/qc/task_status.jsonl",
            "snapshot_line": snapshot_line,
            **origins[task["task_id"]],
        }
        rows.append(row)
    return rows


def copy_snapshot(
    source: Path,
    destination: Path,
    stage: Path,
    *,
    sanitize: bool = False,
) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    original_hash = sha256(source)
    if sanitize:
        text = source.read_text(encoding="utf-8")
        prefix = REPO_ROOT.as_posix()
        text = text.replace(prefix + "/", "").replace(prefix, ".")
        destination.write_text(text, encoding="utf-8")
    else:
        shutil.copy2(source, destination)
    return {
        "source": repo_relative(source),
        "bundled_path": destination.relative_to(stage).as_posix(),
        "source_sha256": original_hash,
        "bundled_sha256": sha256(destination),
        "sanitized_absolute_paths": sanitize,
    }


def write_readme(stage: Path) -> None:
    (stage / "README.md").write_text(
        """# ScienceVision scale-v2 expert-review bundle

This offline package contains 986 repaired candidate tasks, 581 quarantined tasks, all 1,307 referenced assets, QC snapshots, and the Mol*/NiiVue viewers required for review.

This is an expert-review artifact, not a formal release. Independent evaluation is still missing, and every bundled task is marked `formal_release: false`. The default viewer opens the 986 candidate tasks. The quarantine manifest remains separate so blocked material can be inspected without entering the candidate set.

## Open

- macOS: double-click `打开 ScienceVision（Mac）.app`.
- Windows: double-click `打开 ScienceVision（Windows）.exe`.
- Fallback: run `START.command`, `START.cmd`, or `START.sh` with Python 3.

The server binds only to `127.0.0.1`, blocks proxy endpoints, disables directory listings, and has no runtime network dependency. Run `python3 serve_bundle.py --verify-only` to verify the complete package before review.

Use `bundle_manifest.json` for candidates and `bundle_manifest-quarantine.json` for quarantined tasks. Per-task repair and QC states are joined in `qc/task_status.jsonl`; strict source records remain unchanged under `sources/scale-v2/`.
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
        "# ScienceVision scale-v2 task index",
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
                "| # | Task ID | Domain | Family | Repair | Latest QC | Evaluation |",
                "|---:|---|---|---|---|---|---|",
            ]
        )
        for index, task in enumerate(rows, 1):
            status = statuses[task["task_id"]]
            latest = status.get("round2_qc") or status.get("repaired_qc") or {}
            task_id = task["task_id"]
            link = (
                "viewer/sciencevision_tasks.html?manifest="
                f"{manifest.replace('/', '%2F')}&task={task_id}"
            )
            lines.append(
                f"| {index} | [{task_id}]({link}) | {task['domain']} | "
                f"{status.get('family') or ''} | {status.get('repair_status') or ''} | "
                f"{latest.get('decision') or latest.get('grade') or 'n/a'} | "
                f"{status['evaluation_status']} |"
            )
        lines.append("")
    (stage / "TASK_INDEX.md").write_text("\n".join(lines), encoding="utf-8")


def write_checksums(stage: Path) -> tuple[int, int]:
    files = sorted(
        (path for path in stage.rglob("*") if path.is_file() and path.name != "SHA256SUMS"),
        key=lambda path: path.relative_to(stage).as_posix(),
    )
    lines = [f"{sha256(path)}  {path.relative_to(stage).as_posix()}" for path in files]
    (stage / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(files), sum(path.stat().st_size for path in files)


def validate_stage(stage: Path, asset_paths: list[PurePosixPath]) -> None:
    symlinks = [path for path in stage.rglob("*") if path.is_symlink()]
    if symlinks:
        raise ValueError(f"bundle contains symlinks: {symlinks[:5]}")
    missing = [relative.as_posix() for relative in asset_paths if not stage.joinpath(*relative.parts).is_file()]
    if missing:
        raise ValueError(f"bundle is missing assets: {missing[:5]}")
    for path in stage.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        relative = path.relative_to(stage)
        if relative.parts and relative.parts[0] in ALLOWED_ASSET_ROOTS:
            continue
        text = path.read_text(encoding="utf-8", errors="strict")
        if "/Users/" in text or "file://" in text:
            raise ValueError(f"absolute local path leaked into bundle text: {relative}")
        if path.suffix.lower() == ".html" and re.search(
            r"(?:src|href)\s*=\s*['\"]https?://", text, flags=re.IGNORECASE
        ):
            raise ValueError(f"external HTML dependency in {relative}")


def source_snapshots(stage: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    scale_dir = REPO_ROOT / "QA-Gen/tasks/scale-v2"
    sources = [
        *task_sources("candidate"),
        *task_sources("quarantine"),
        scale_dir / "scale-v2-build.json",
        scale_dir / "scale-v2-decisions.jsonl",
    ]
    for source in sources:
        relative = source.relative_to(scale_dir)
        destination = stage / "sources/scale-v2" / relative
        records.append(copy_snapshot(source, destination, stage))

    for source in (
        REPO_ROOT / "docs/qa-gen/SCALE_V1_REVIEWED_20260808_LOCK.json",
        REPO_ROOT / "docs/qa-gen/SCALE_V2_BUILD_20260808_LOCK.json",
    ):
        records.append(copy_snapshot(source, stage / "sources/locks" / source.name, stage))

    schema = (
        REPO_ROOT
        / "QA-Gen/ScienceVisionTaskGenerator/schemas/sciencevision_task.schema.json"
    )
    records.append(copy_snapshot(schema, stage / "sources/schema" / schema.name, stage))

    for batch in ("scale-v2-repaired-20260808", "scale-v2-round2-20260808"):
        source_dir = REPO_ROOT / "outputs/qa-qc" / batch
        for source in sorted(source_dir.iterdir()):
            if not source.is_file():
                continue
            sanitize = batch == "scale-v2-round2-20260808"
            destination = stage / "qc" / batch / source.name
            records.append(copy_snapshot(source, destination, stage, sanitize=sanitize))
    return records


def build(args: argparse.Namespace) -> dict[str, Any]:
    output = args.output.resolve()
    validate_output(output)
    if not args.reference.is_dir() or not args.asset_source.is_dir():
        raise FileNotFoundError("reference bundle or asset source is missing")

    scale_dir = REPO_ROOT / "QA-Gen/tasks/scale-v2"
    build_manifest, build_lock = verify_build_manifest(scale_dir)
    candidates, candidate_origins = load_tasks("candidate")
    quarantined, quarantine_origins = load_tasks("quarantine")
    schema_path = (
        REPO_ROOT
        / "QA-Gen/ScienceVisionTaskGenerator/schemas/sciencevision_task.schema.json"
    )
    validate_schema(candidates + quarantined, schema_path)

    decision_rows, decisions = load_decisions(scale_dir / "scale-v2-decisions.jsonl")
    repaired_rows = read_jsonl(
        REPO_ROOT / "outputs/qa-qc/scale-v2-repaired-20260808/task_classifications.jsonl"
    )
    round2_rows = read_jsonl(
        REPO_ROOT / "outputs/qa-qc/scale-v2-round2-20260808/task_classifications.jsonl"
    )
    quarantine_audit_rows = read_jsonl(
        REPO_ROOT / "outputs/qa-qc/scale-v2-round2-20260808/quarantine_audit.jsonl"
    )
    repaired = keyed(repaired_rows, "repaired QC")
    round2 = keyed(round2_rows, "round2 QC")
    quarantine_audit = keyed(quarantine_audit_rows, "quarantine audit")
    validate_collections(
        candidates, quarantined, decisions, repaired, round2, quarantine_audit
    )
    asset_paths = safe_asset_paths(candidates + quarantined)

    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent))
    try:
        copy_runtime(stage, args.reference)
        transform_viewer(stage)
        sanitize_runtime_protocol_literals(stage)
        asset_bytes = copy_assets(stage, args.asset_source, asset_paths)

        candidate_bundle_rows = bundle_rows(candidates, "candidate", candidate_origins)
        quarantine_bundle_rows = bundle_rows(
            quarantined, "quarantine", quarantine_origins
        )
        write_jsonl(stage / "tasks/candidate_tasks.jsonl", candidate_bundle_rows)
        write_jsonl(stage / "tasks/quarantine_tasks.jsonl", quarantine_bundle_rows)

        status_rows = build_status_rows(
            candidates,
            "candidate",
            decisions,
            repaired,
            round2,
            quarantine_audit,
        ) + build_status_rows(
            quarantined,
            "quarantine",
            decisions,
            repaired,
            round2,
            quarantine_audit,
        )
        write_jsonl(stage / "qc/task_status.jsonl", status_rows)
        statuses = keyed(status_rows, "bundle QC status")

        candidate_manifest = {
            "kind": "sciencevision-task-bundle",
            "schema_version": "sciencevision.task_bundle.v2",
            "bundle_id": BUNDLE_ID,
            "title": "scale-v2 expert-review candidates",
            "disposition": "candidate",
            "formal_release": False,
            "tasks_jsonl": "/tasks/candidate_tasks.jsonl",
            "task_count": len(candidates),
            "task_index": "/TASK_INDEX.md",
            "qc_status_jsonl": "/qc/task_status.jsonl",
            "related_manifests": [
                {
                    "label": "quarantine (581)",
                    "disposition": "quarantine",
                    "path": "/bundle_manifest-quarantine.json",
                }
            ],
        }
        quarantine_manifest = {
            **candidate_manifest,
            "title": "scale-v2 expert-review quarantine",
            "disposition": "quarantine",
            "tasks_jsonl": "/tasks/quarantine_tasks.jsonl",
            "task_count": len(quarantined),
            "related_manifests": [
                {
                    "label": "candidates (986)",
                    "disposition": "candidate",
                    "path": "/bundle_manifest.json",
                }
            ],
        }
        write_json(stage / "bundle_manifest.json", candidate_manifest)
        write_json(stage / "bundle_manifest-quarantine.json", quarantine_manifest)

        snapshot_records = source_snapshots(stage)
        write_readme(stage)
        write_index(stage, candidates, quarantined, statuses)
        task_ids = [row["task_id"] for row in candidates + quarantined]
        candidate_ids = [row["task_id"] for row in candidates]
        quarantine_ids = [row["task_id"] for row in quarantined]
        write_json(
            stage / "SELECTION.json",
            {
                "schema_version": "sciencevision.scale_v2.selection.v1",
                "candidate_count": len(candidate_ids),
                "quarantine_count": len(quarantine_ids),
                "total_count": len(task_ids),
                "partition": "strict_disjoint_complete",
                "candidate_task_ids_sha256": stable_hash(candidate_ids),
                "quarantine_task_ids_sha256": stable_hash(quarantine_ids),
                "all_task_ids_sha256": stable_hash(task_ids),
                "asset_count": len(asset_paths),
                "asset_paths_sha256": stable_hash(path.as_posix() for path in asset_paths),
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
        write_json(
            stage / "BUNDLE_METADATA.json",
            {
                "schema_version": "sciencevision.expert_bundle.metadata.v1",
                "bundle_id": BUNDLE_ID,
                "generated_at": GENERATED_AT,
                "source_dataset": "scale-v1",
                "output_dataset": "scale-v2",
                "git_commit": git_commit,
                "task_counts": {
                    "candidate": len(candidates),
                    "quarantine": len(quarantined),
                    "total": len(candidates) + len(quarantined),
                },
                "asset_count": len(asset_paths),
                "asset_bytes": asset_bytes,
                "offline": True,
                "zero_symlinks": True,
                "runtime": {
                    "native_macos": "macOS 12 or later",
                    "native_windows": "bundled launcher",
                    "fallback": "Python 3",
                    "bind": "127.0.0.1",
                },
                "formal_release": False,
            },
        )
        write_json(
            stage / "MANIFEST.json",
            {
                "schema_version": "sciencevision.expert_bundle.manifest.v1",
                "bundle_id": BUNDLE_ID,
                "generated_at": GENERATED_AT,
                "git_commit": git_commit,
                "formal_release": False,
                "source_build": {
                    "path": "QA-Gen/tasks/scale-v2/scale-v2-build.json",
                    "sha256": sha256(scale_dir / "scale-v2-build.json"),
                    "schema_version": build_manifest.get("schema_version"),
                    "lock_path": "docs/qa-gen/SCALE_V2_BUILD_20260808_LOCK.json",
                    "lock_sha256": sha256(
                        REPO_ROOT / "docs/qa-gen/SCALE_V2_BUILD_20260808_LOCK.json"
                    ),
                    "lock_schema_version": build_lock.get("schema_version"),
                },
                "strict_schema": {
                    "path": repo_relative(schema_path),
                    "sha256": sha256(schema_path),
                    "validated_task_count": len(candidates) + len(quarantined),
                },
                "joins": {
                    "decision_count": len(decision_rows),
                    "repaired_qc_count": len(repaired_rows),
                    "round2_qc_count": len(round2_rows),
                    "quarantine_audit_count": len(quarantine_audit_rows),
                    "status": "strict_1_to_1",
                },
                "assets": {
                    "count": len(asset_paths),
                    "bytes": asset_bytes,
                    "paths_sha256": stable_hash(path.as_posix() for path in asset_paths),
                },
                "source_snapshots": snapshot_records,
            },
        )

        validate_stage(stage, asset_paths)
        file_count, payload_bytes = write_checksums(stage)
        # SHA256SUMS is intentionally outside its own inventory.
        checksum_hash = sha256(stage / "SHA256SUMS")
        if output.exists():
            output.rmdir()
        os.replace(stage, output)
        return {
            "output": str(output),
            "candidate_tasks": len(candidates),
            "quarantine_tasks": len(quarantined),
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
