"""Phase 2 / Agent E2 — Pipeline-write tools.

Three write actions. Every tool defaults ``dry_run=true`` (the safety rule):

  * sv_classify_v_batch       — append timestamped JSONL under scripts/v_classify/out/
  * sv_rebuild_matched_index  — rewrite datasets/_matched/*.json (dry-run-by-default)
  * sv_run_m3d_pipeline       — wrap tools/m3d_pipeline_cli.py (dry-run-by-default)

All handlers are pure: take a Mapping, return a new dict. Failures come back as
``{"error": "..."}`` envelopes; nothing escapes across the MCP boundary.

Tests inject ``_impl=...`` to avoid real LLM calls / real subprocess work; the
``SV_E2E=1`` env gate is the only path to live execution.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from sci3d_agent.mcp.registry import ToolSpec
from sci3d_agent.mcp.runtime.paths import repo_root

logger = logging.getLogger("sciencevision.pipeline_write")


# ---------------------------------------------------------------------------
# Schema loading (mirrors pipeline_read._load_schema for consistency)
# ---------------------------------------------------------------------------

_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"


def _load_schema(name: str) -> dict:
    path = _SCHEMA_DIR / f"{name}.json"
    if not path.exists():
        return {"type": "object"}
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Pricing constants for sv_classify_v_batch cost estimate.
# Order-of-magnitude only — Mindra/Gemini billing is upstream-truth.
# Gemini Pro reference USD per 1M tokens.
# ---------------------------------------------------------------------------

_USD_PER_INPUT_TOKEN = 1.25 / 1_000_000.0
_USD_PER_OUTPUT_TOKEN = 5.00 / 1_000_000.0

_V_KEYS = ("V1", "V2", "V3", "V4", "V5", "V6", "none")
_CLASSIFY_V_DEFAULT_MODEL = "gemini-3.1-pro-preview"

_DEFAULT_MATCH_OUTPUT_DIR = "datasets/_matched"


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _resolve_path(p: str | Path) -> Path:
    """Resolve an input string to a Path that is absolute (rooted at repo_root for relatives)."""
    path = Path(p)
    if not path.is_absolute():
        path = repo_root() / path
    return path


# ===========================================================================
# Tool 1 — sv_classify_v_batch
# ===========================================================================


def _build_chat_client_default() -> Any:
    """Construct the real ChatClient. Tests never call this — they inject ``_impl``."""
    base_url = os.environ.get("ANTHROPIC_BASE_URL") or os.environ.get("MINDRA_BASE_URL")
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("MINDRA_API")
    if not base_url or not api_key:
        raise RuntimeError(
            "missing ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY (or MINDRA_*) in env",
        )
    client_mod = importlib.import_module("scripts.v_classify.client")
    return client_mod.ChatClient(base_url, api_key)


def _classify_one_default_impl(
    client: Any,
    model: str,
    rec: Mapping[str, Any],
    max_attempts: int = 3,
) -> dict:
    classify_mod = importlib.import_module("scripts.v_classify.classify_qa")
    return classify_mod.classify_one(client, model, dict(rec), max_attempts)


def _load_records_default(dataset: str, *, skip_no_answer: bool) -> list[dict]:
    classify_mod = importlib.import_module("scripts.v_classify.classify_qa")
    return classify_mod.load_records(dataset, skip_no_answer=skip_no_answer)


def _sample_records_default(records: list[dict], n: int, seed: int) -> list[dict]:
    classify_mod = importlib.import_module("scripts.v_classify.classify_qa")
    return classify_mod.sample_records(records, n, seed)


def _already_done_default(out_path: Path) -> set[str]:
    classify_mod = importlib.import_module("scripts.v_classify.classify_qa")
    return classify_mod.already_done(out_path)


def _resolve_batch_size(n_eligible: int, limit: Any, sample: Any) -> int:
    """Resolve the effective sample size from limit / sample / total."""
    if limit is not None:
        return max(0, min(int(limit), n_eligible))
    if sample is not None:
        return max(0, min(int(round(float(sample) * n_eligible)), n_eligible))
    return n_eligible


def _per_class_counts(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts = {k: 0 for k in _V_KEYS}
    for row in rows:
        if row.get("status") != "ok":
            continue
        dv = row.get("dominant_v", "none")
        if dv not in counts:
            dv = "none"
        counts[dv] = counts[dv] + 1
    return counts


def _estimate_cost_usd(rows: Iterable[Mapping[str, Any]]) -> float:
    total = 0.0
    for r in rows:
        total += int(r.get("prompt_tokens") or 0) * _USD_PER_INPUT_TOKEN
        total += int(r.get("completion_tokens") or 0) * _USD_PER_OUTPUT_TOKEN
    return round(total, 6)


def _default_output_path(dataset: str) -> Path:
    return repo_root() / "scripts" / "v_classify" / "out" / f"{dataset}__{_utc_stamp()}.jsonl"


def _read_existing_qids(out_path: Path, *, _already_done: Callable[[Path], set[str]]) -> set[str]:
    if not out_path.exists():
        return set()
    return _already_done(out_path)


def _handle_classify_v_batch(
    args: Mapping[str, Any],
    *,
    _load_records: Callable[..., list[dict]] | None = None,
    _sample_records: Callable[[list[dict], int, int], list[dict]] | None = None,
    _build_client: Callable[[], Any] | None = None,
    _classify_one: Callable[[Any, str, Mapping[str, Any], int], dict] | None = None,
    _already_done: Callable[[Path], set[str]] | None = None,
    _now: Callable[[], float] | None = None,
) -> dict:
    load_records = _load_records or _load_records_default
    sample_records = _sample_records or _sample_records_default
    classify_one = _classify_one or _classify_one_default_impl
    already_done = _already_done or _already_done_default
    now = _now or time.monotonic

    dataset = args.get("dataset")
    if not isinstance(dataset, str) or not dataset:
        return {"error": "dataset (str) is required"}

    model = args.get("model") or _CLASSIFY_V_DEFAULT_MODEL
    seed = int(args.get("seed", 0) or 0)
    max_concurrency = int(args.get("max_concurrency", 1) or 1)
    if max_concurrency < 1 or max_concurrency > 8:
        return {"error": f"max_concurrency must be in [1, 8]; got {max_concurrency}"}

    try:
        records = load_records(dataset, skip_no_answer=True)
    except SystemExit as exc:
        return {"error": f"load_records failed: {exc!s}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"load_records raised: {exc!r}"}

    n_total = len(records)
    target_n = _resolve_batch_size(n_total, args.get("limit"), args.get("sample"))
    if target_n <= 0:
        return {"error": "resolved sample size is zero"}

    try:
        sampled = sample_records(records, target_n, seed)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"sample_records raised: {exc!r}"}

    out_path = Path(args["output_jsonl"]) if args.get("output_jsonl") else _default_output_path(dataset)
    if not out_path.is_absolute():
        out_path = repo_root() / out_path

    if args.get("output_jsonl") and out_path.exists() and out_path.stat().st_size > 0:
        return {
            "error": (
                f"refusing to overwrite existing non-empty file: {out_path}; "
                "remove it or pick a different output_jsonl."
            ),
        }

    done_qids = _read_existing_qids(out_path, _already_done=already_done)
    todo = [r for r in sampled if r.get("qid") not in done_qids]

    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not todo:
        existing_rows = _read_jsonl_rows(out_path)
        return {
            "output_path": str(out_path),
            "n_items": len(sampled),
            "n_done": len(done_qids),
            "per_class_counts": _per_class_counts(existing_rows),
            "total_cost_usd": _estimate_cost_usd(existing_rows),
            "elapsed_s": 0.0,
        }

    client: Any
    try:
        if _build_client is not None:
            client = _build_client()
        else:
            client = _build_chat_client_default()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"chat-client init failed: {exc!s}"}

    t0 = now()
    write_lock = threading.Lock()
    results: list[dict] = []

    def _run_one(rec: Mapping[str, Any]) -> dict:
        try:
            return classify_one(client, model, rec, 3)
        except Exception as exc:  # noqa: BLE001
            return {
                "qid": rec.get("qid"),
                "dataset": rec.get("dataset"),
                "status": "error",
                "error": repr(exc),
            }

    with out_path.open("a", encoding="utf-8") as fh:
        if max_concurrency == 1:
            for rec in todo:
                row = _run_one(rec)
                results.append(row)
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
        else:
            with ThreadPoolExecutor(max_workers=max_concurrency) as ex:
                futures = {ex.submit(_run_one, r): r for r in todo}
                for fut in as_completed(futures):
                    row = fut.result()
                    with write_lock:
                        results.append(row)
                        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                        fh.flush()

    elapsed = now() - t0

    all_rows = _read_jsonl_rows(out_path)
    logger.info(
        "classify_v_batch wrote %d rows to %s (dataset=%s)",
        len(results),
        out_path,
        dataset,
    )
    return {
        "output_path": str(out_path),
        "n_items": len(sampled),
        "n_done": len(all_rows),
        "per_class_counts": _per_class_counts(all_rows),
        "total_cost_usd": _estimate_cost_usd(all_rows),
        "elapsed_s": round(float(elapsed), 4),
    }


def _read_jsonl_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


# ===========================================================================
# Tool 2 — sv_rebuild_matched_index
# ===========================================================================


def _canonical_json_bytes(obj: Any) -> bytes:
    """Stable canonical encoding (sorted keys, no whitespace) for SHA comparison."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _collect_matched_default() -> dict[str, list[Any]]:
    mod = importlib.import_module("scripts.match.build_matched_3d_qa")
    return mod.collect(repo_root())


def _build_payload(name: str, records: list[Any], generated_at: str) -> dict:
    """Mirror scripts.match.build_matched_3d_qa.write_outputs payload shape."""
    serialized = [asdict(r) if hasattr(r, "__dataclass_fields__") else dict(r) for r in records]
    return {
        "schema_version": "1.0",
        "dataset": name,
        "generated_at": generated_at,
        "policy": "strict_present_on_disk",
        "count": len(serialized),
        "records": serialized,
    }


def _record_diff(new_records: list, old_records: list) -> tuple[int, int, int]:
    """Compute (added, removed, changed) keyed by qid."""
    def _qid(r: Any) -> str | None:
        if hasattr(r, "qid"):
            return r.qid
        if isinstance(r, Mapping):
            return r.get("qid")
        return None

    def _to_dict(r: Any) -> dict:
        if hasattr(r, "__dataclass_fields__"):
            return asdict(r)
        return dict(r) if isinstance(r, Mapping) else {}

    new_by = {_qid(r): _to_dict(r) for r in new_records if _qid(r)}
    old_by = {_qid(r): _to_dict(r) for r in old_records if _qid(r)}

    new_qids = set(new_by.keys())
    old_qids = set(old_by.keys())
    added = len(new_qids - old_qids)
    removed = len(old_qids - new_qids)
    changed = sum(
        1 for q in (new_qids & old_qids) if new_by[q] != old_by[q]
    )
    return added, removed, changed


def _handle_rebuild_matched_index(
    args: Mapping[str, Any],
    *,
    _collect: Callable[[], dict[str, list[Any]]] | None = None,
) -> dict:
    collect = _collect or _collect_matched_default

    dry_run = bool(args.get("dry_run", True))
    requested = args.get("datasets")
    requested_set = set(requested) if requested else None

    output_dir_arg = args.get("output_dir") or _DEFAULT_MATCH_OUTPUT_DIR
    out_dir = _resolve_path(output_dir_arg)

    try:
        grouped = collect()
    except ModuleNotFoundError as exc:
        return {"error": f"match builder not importable: {exc!s}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"match builder failed: {exc!r}"}

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    files_plan: list[dict] = []
    total_records_planned = 0
    n_changed = 0

    for name, records in grouped.items():
        if requested_set and name not in requested_set:
            continue
        payload = _build_payload(name, records, generated_at)
        # New SHA computed over a generated_at-stripped payload to avoid spurious diff
        payload_for_hash = {k: v for k, v in payload.items() if k != "generated_at"}
        new_bytes = _canonical_json_bytes(payload_for_hash)
        new_sha = _sha256_hex(new_bytes)

        out_path = out_dir / f"{name}.json"
        old_records: list = []
        old_sha = ""
        if out_path.exists():
            try:
                existing = json.loads(out_path.read_text())
            except json.JSONDecodeError:
                existing = {"records": []}
            old_for_hash = {k: v for k, v in existing.items() if k != "generated_at"}
            old_sha = _sha256_hex(_canonical_json_bytes(old_for_hash))
            old_records = existing.get("records", []) or []

        added, removed, changed = _record_diff(records, old_records)
        changed_flag = new_sha != old_sha
        if changed_flag:
            n_changed += 1
        total_records_planned += len(records)
        files_plan.append(
            {
                "path": str(out_path),
                "new_sha": new_sha,
                "old_sha": old_sha,
                "added": added,
                "removed": removed,
                "changed": changed,
            }
        )

    if dry_run:
        return {
            "plan": {"files_to_write": files_plan},
            "summary": {
                "n_files": len(files_plan),
                "n_changed": n_changed,
                "total_records_planned": total_records_planned,
            },
        }

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for name, records in grouped.items():
        if requested_set and name not in requested_set:
            continue
        payload = _build_payload(name, records, generated_at)
        out_path = out_dir / f"{name}.json"
        with out_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        written.append(str(out_path))

    index_payload = {
        "schema_version": "1.0",
        "generated_at": generated_at,
        "policy": "strict_present_on_disk",
        "scope": "tier1_3d_grounded",
        "files": [
            {
                "dataset": name,
                "path_repo": str((out_dir / f"{name}.json")),
                "count": len(records),
            }
            for name, records in grouped.items()
            if requested_set is None or name in requested_set
        ],
        "total": sum(
            len(records) for name, records in grouped.items()
            if requested_set is None or name in requested_set
        ),
    }
    idx_path = out_dir / "index.json"
    with idx_path.open("w", encoding="utf-8") as fh:
        json.dump(index_payload, fh, indent=2, ensure_ascii=False)
    written.append(str(idx_path))

    logger.info("rebuild_matched_index wrote %d files to %s", len(written), out_dir)
    return {
        "written": written,
        "summary": {
            "n_files": len(written),
            "total_records": total_records_planned,
        },
    }


# ===========================================================================
# Tool 3 — sv_run_m3d_pipeline
# ===========================================================================


def _default_m3d_output_dir() -> Path:
    return repo_root() / "out_samples" / "m3d" / _utc_stamp()


def _build_m3d_command(input_path: Path, output_dir: Path, config: str | None) -> list[str]:
    """Construct the CLI invocation for tools/m3d_pipeline_cli.py.

    `config` selects the subcommand. Defaults to 'prep' (NIfTI -> .npy).
    """
    cli_path = repo_root() / "tools" / "m3d_pipeline_cli.py"
    sub = (config or "prep").strip()
    if sub == "prep":
        out_file = output_dir / (input_path.stem + ".npy")
        return [sys.executable, str(cli_path), "prep", str(input_path), str(out_file)]
    if sub == "derive":
        out_file = output_dir / (input_path.stem + ".nii.gz")
        return [sys.executable, str(cli_path), "derive", str(input_path), str(out_file)]
    return [sys.executable, str(cli_path), sub, str(input_path), str(output_dir)]


def _planned_output_files(input_path: Path, output_dir: Path, config: str | None) -> list[str]:
    sub = (config or "prep").strip()
    if sub == "prep":
        return [str(output_dir / (input_path.stem + ".npy"))]
    if sub == "derive":
        return [str(output_dir / (input_path.stem + ".nii.gz"))]
    return [str(output_dir)]


def _handle_run_m3d_pipeline(
    args: Mapping[str, Any],
    *,
    _runner: Callable[[list[str], Path], "subprocess.CompletedProcess"] | None = None,
    _now: Callable[[], float] | None = None,
) -> dict:
    now = _now or time.monotonic

    input_arg = args.get("input")
    if not isinstance(input_arg, str) or not input_arg:
        return {"error": "input (str) is required"}

    input_path = _resolve_path(input_arg)
    config = args.get("config") or "prep"
    dry_run = bool(args.get("dry_run", True))

    if args.get("output_dir"):
        output_dir = _resolve_path(args["output_dir"])
    else:
        output_dir = _default_m3d_output_dir()

    cmd = _build_m3d_command(input_path, output_dir, config)
    working_dir = repo_root()

    if dry_run:
        return {
            "commands": [cmd],
            "outputs_planned": _planned_output_files(input_path, output_dir, config),
            "working_dir": str(working_dir),
        }

    output_dir.mkdir(parents=True, exist_ok=True)

    runner = _runner or _run_subprocess_default
    t0 = now()
    try:
        proc = runner(cmd, working_dir)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"subprocess invocation failed: {exc!r}", "command": cmd}
    elapsed = now() - t0

    stdout = (proc.stdout or "") if isinstance(proc.stdout, str) else (proc.stdout or b"").decode(
        "utf-8", "replace"
    )
    tail = stdout[-4096:]
    artifacts: list[str] = []
    if output_dir.exists():
        artifacts = sorted(str(p) for p in output_dir.rglob("*") if p.is_file())

    logger.info(
        "run_m3d_pipeline exit=%s output_dir=%s artifacts=%d",
        proc.returncode,
        output_dir,
        len(artifacts),
    )
    return {
        "stdout_tail": tail,
        "artifacts": artifacts,
        "exit_code": int(proc.returncode),
        "elapsed_s": round(float(elapsed), 4),
    }


def _run_subprocess_default(cmd: list[str], cwd: Path) -> "subprocess.CompletedProcess":
    return subprocess.run(  # noqa: S603 — args are constructed from validated paths
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )


# ===========================================================================
# Tool registration
# ===========================================================================


TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="sv_classify_v_batch",
        description=(
            "Batch-classify V1..V6 on a tier-1 dataset; appends timestamped JSONL "
            "under scripts/v_classify/out/. Idempotent per qid; no overwrite of "
            "non-empty user-supplied output_jsonl."
        ),
        input_schema=_load_schema("sv_classify_v_batch"),
        handler=_handle_classify_v_batch,
        category="E",
    ),
    ToolSpec(
        name="sv_rebuild_matched_index",
        description=(
            "Rebuild datasets/_matched/*.json from the on-disk asset tree. "
            "dry_run=true (default) returns a per-file plan with content-hash diff."
        ),
        input_schema=_load_schema("sv_rebuild_matched_index"),
        handler=_handle_rebuild_matched_index,
        category="E",
    ),
    ToolSpec(
        name="sv_run_m3d_pipeline",
        description=(
            "Invoke tools/m3d_pipeline_cli.py with timestamped output_dir. "
            "dry_run=true (default) returns the planned command without execution."
        ),
        input_schema=_load_schema("sv_run_m3d_pipeline"),
        handler=_handle_run_m3d_pipeline,
        category="E",
    ),
]
