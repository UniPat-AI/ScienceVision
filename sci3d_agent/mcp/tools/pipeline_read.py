"""Phase 1 / Agent E1 — Pipeline-read tools.

Five read-only tools wrapping existing CLI / library code in the repo:

  * sv_score_astronomy              — wraps tools/score_astronomy.py
  * sv_classify_v                   — single-record LLM V-class call (Mindra→Gemini)
  * sv_get_v_classify_pilot_summary — pre-computed pilot stats (frozen)
  * sv_validate_matched_index       — audit datasets/_matched/*.json vs on-disk
  * sv_run_v_validation             — wraps tools/v_validation/

All handlers are pure: they take a Mapping and return a new dict. No exceptions
escape — failures are returned as ``{"error": "..."}`` envelopes so the MCP
server's safe-wrapper does not need to translate them.

LLM handlers default to ``mock=True``; tests run without burning credits.
Set ``SV_E2E=1`` to opt-in to real network calls.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from sci3d_agent.mcp.registry import ToolSpec
from sci3d_agent.mcp.runtime.paths import repo_root

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema loading
# ---------------------------------------------------------------------------

_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"


def _load_schema(name: str) -> dict:
    path = _SCHEMA_DIR / f"{name}.json"
    if not path.exists():
        # Defensive: return a permissive schema rather than crashing import.
        return {"type": "object"}
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Frozen pilot constants (per /Users/sue/Research/ScienceVision/docs/v_classify/REPORT_pilot100.md)
# ---------------------------------------------------------------------------

_PILOT_SUMMARY_FROZEN: dict[str, dict[str, Any]] = {
    "scanqa": {
        "name": "scanqa",
        "n": 100,
        "per_class_pct": {
            "V1": 0.0, "V2": 0.0, "V3": 0.0, "V4": 0.0,
            "V5": 0.0, "V6": 0.0, "none": 100.0,
        },
        "source_run_id": "pilot100",
        "dated": "2026-05-19",
    },
    "sqa3d": {
        "name": "sqa3d",
        "n": 100,
        "per_class_pct": {
            "V1": 0.0, "V2": 3.0, "V3": 0.0, "V4": 0.0,
            "V5": 0.0, "V6": 0.0, "none": 97.0,
        },
        "source_run_id": "pilot100",
        "dated": "2026-05-19",
    },
    "m3d-refseg": {
        "name": "m3d-refseg",
        "n": 100,
        "per_class_pct": {
            "V1": 0.0, "V2": 0.0, "V3": 100.0, "V4": 0.0,
            "V5": 0.0, "V6": 0.0, "none": 0.0,
        },
        "source_run_id": "pilot100",
        "dated": "2026-05-19",
    },
}


# ---------------------------------------------------------------------------
# sv_score_astronomy
# ---------------------------------------------------------------------------


def _score_astronomy_default_impl(question_id: str, predicted_answer: Any) -> dict:
    """Invoke tools/score_astronomy.py via its in-process API."""
    try:
        mod = importlib.import_module("tools.score_astronomy")
    except Exception as exc:  # noqa: BLE001
        return {"error": f"score_astronomy import failed: {exc!r}"}

    try:
        questions = mod.load_questions()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"load_questions failed: {exc!r}"}

    matching = [q for q in questions if q.get("id") == question_id]
    if not matching:
        return {"error": f"unknown question_id: {question_id!r}"}
    q = matching[0]

    try:
        truth = mod.compute_truth(q)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"compute_truth failed: {exc!r}"}

    if isinstance(truth, dict) and "error" in truth:
        return {"error": str(truth["error"])}

    try:
        result = mod.grade(q, predicted_answer, truth)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"grade failed: {exc!r}"}

    return {"question": q, "truth": truth, "result": result}


def _handle_score_astronomy(
    args: Mapping[str, Any],
    *,
    _impl: Callable[[str, Any], dict] | None = None,
) -> dict:
    question_id = args.get("question_id")
    if not isinstance(question_id, str) or not question_id:
        return {"error": "question_id (str) is required"}
    if "predicted_answer" not in args:
        return {"error": "predicted_answer is required"}
    predicted = args["predicted_answer"]
    gt_version = args.get("ground_truth_version", "V6")

    impl = _impl or _score_astronomy_default_impl
    out = impl(question_id, predicted)
    if "error" in out:
        return {"error": out["error"], "gt_version": gt_version}

    q = out["question"]
    truth = out["truth"]
    result = out["result"]

    # Build a stable envelope.
    score = 1.0 if result.get("pass") else 0.0
    reference_answer: Any
    if q.get("v_class") == "V2":
        reference_answer = truth.get("ground_truth")
    elif q.get("v_class") == "V4":
        reference_answer = truth.get("ground_truth_helio_RG_au")
    elif q.get("v_class") == "V6":
        reference_answer = truth.get("ground_truth_max_mi_deg")
    else:
        reference_answer = None

    return {
        "score": score,
        "pass": bool(result.get("pass")),
        "breakdown": dict(result),
        "reference_answer": reference_answer,
        "gt_version": q.get("v_class", gt_version),
    }


# ---------------------------------------------------------------------------
# sv_classify_v
# ---------------------------------------------------------------------------

_CLASSIFY_V_DEFAULT_MODEL = "gemini-3.1-pro-preview"


def _classify_v_mock_impl(record: Mapping[str, Any], model: str) -> dict:
    """Deterministic mock — never touches network. Returns 'none' V-class."""
    return {
        "V_class": "none",
        "reasoning_class": 0.0,
        "knowledge_class": 0.0,
        "rationale": "mock_classification",
        "model_used": model,
        "token_cost": {"input": 0, "output": 0, "usd": 0.0},
        "latency_ms": 0,
    }


def _classify_v_live_impl(record: Mapping[str, Any], model: str) -> dict:
    """Real LLM call via Mindra OpenAI-compat. Reuses scripts/v_classify/."""
    base_url = os.environ.get("ANTHROPIC_BASE_URL") or os.environ.get("MINDRA_BASE_URL")
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("MINDRA_API")
    if not base_url or not api_key:
        return {
            "error": "missing ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY (or MINDRA_*) in env",
        }

    try:
        client_mod = importlib.import_module("scripts.v_classify.client")
        prompt_mod = importlib.import_module("scripts.v_classify.prompt")
        classify_mod = importlib.import_module("scripts.v_classify.classify_qa")
    except Exception as exc:  # noqa: BLE001
        return {"error": f"scripts.v_classify import failed: {exc!r}"}

    client = client_mod.ChatClient(base_url, api_key)
    user_msg = prompt_mod.build_user_msg(dict(record))
    t0 = time.monotonic()
    try:
        res = client.complete_with_retry(
            model=model,
            system=prompt_mod.SYSTEM_PROMPT,
            user=user_msg,
            max_attempts=3,
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"chat.complete failed: {exc!r}"}
    latency_ms = int((time.monotonic() - t0) * 1000.0)

    parsed, err = classify_mod.parse_classification(res.content)
    if parsed is None:
        return {"error": f"classifier output invalid: {err}", "raw": res.content[:500]}

    return {
        "V_class": parsed.get("dominant_v", "none"),
        "reasoning_class": parsed.get("reasoning", 0.0),
        "knowledge_class": parsed.get("knowledge", 0.0),
        "rationale": parsed.get("rationale", ""),
        "model_used": model,
        "token_cost": {
            "input": int(res.prompt_tokens),
            "output": int(res.completion_tokens),
            "usd": 0.0,  # pricing not modeled here
        },
        "latency_ms": latency_ms,
        "axes": {k: parsed[k] for k in ("v1", "v2", "v3", "v4", "v5", "v6") if k in parsed},
    }


def _lookup_record_by_qid(qid: str) -> dict | None:
    """Find a record across datasets/_matched/*.json by qid."""
    root = repo_root() / "datasets" / "_matched"
    index_path = root / "index.json"
    if not index_path.exists():
        return None
    try:
        index = json.loads(index_path.read_text())
    except json.JSONDecodeError:
        return None
    for entry in index.get("files", []):
        ds_path = root / Path(entry["path_repo"]).name
        if not ds_path.exists():
            continue
        try:
            data = json.loads(ds_path.read_text())
        except json.JSONDecodeError:
            continue
        for rec in data.get("records", []):
            if rec.get("qid") == qid:
                return rec
    return None


def _handle_classify_v(
    args: Mapping[str, Any],
    *,
    _impl: Callable[[Mapping[str, Any], str], dict] | None = None,
) -> dict:
    qid = args.get("qid")
    record = args.get("record")
    if record is None and not qid:
        return {"error": "either qid or record is required"}

    if record is None:
        rec = _lookup_record_by_qid(str(qid))
        if rec is None:
            return {"error": f"qid not found in datasets/_matched: {qid!r}"}
    else:
        if not isinstance(record, Mapping):
            return {"error": "record must be an object"}
        rec = dict(record)

    model = args.get("model", _CLASSIFY_V_DEFAULT_MODEL)
    include_rk = bool(args.get("with_reasoning_knowledge", True))

    if _impl is not None:
        impl = _impl
    elif os.environ.get("SV_E2E", "").strip() == "1":
        impl = _classify_v_live_impl
    else:
        impl = _classify_v_mock_impl

    out = impl(rec, model)
    if "error" in out:
        return {"error": out["error"], "model_used": model}

    # Enforce envelope shape — copy out keys so we don't mutate impl result.
    envelope: dict[str, Any] = {
        "V_class": out.get("V_class", "none"),
        "rationale": out.get("rationale", ""),
        "model_used": out.get("model_used", model),
        "token_cost": dict(out.get("token_cost") or {"input": 0, "output": 0, "usd": 0.0}),
        "latency_ms": int(out.get("latency_ms", 0) or 0),
    }
    if include_rk:
        envelope["reasoning_class"] = out.get("reasoning_class", 0.0)
        envelope["knowledge_class"] = out.get("knowledge_class", 0.0)
    if "axes" in out:
        envelope["axes"] = dict(out["axes"])
    return envelope


# ---------------------------------------------------------------------------
# sv_get_v_classify_pilot_summary
# ---------------------------------------------------------------------------


def _deep_copy_pilot_entry(entry: Mapping[str, Any]) -> dict:
    """Deep-copy a pilot entry; nested per_class_pct must be fresh per call."""
    return {
        "name": entry["name"],
        "n": entry["n"],
        "per_class_pct": dict(entry["per_class_pct"]),
        "source_run_id": entry["source_run_id"],
        "dated": entry["dated"],
    }


def _handle_pilot_summary(args: Mapping[str, Any]) -> dict:
    requested = args.get("dataset")
    keys = list(_PILOT_SUMMARY_FROZEN.keys())
    if requested:
        if requested not in _PILOT_SUMMARY_FROZEN:
            return {"error": f"unknown dataset: {requested!r}"}
        keys = [requested]
    return {"datasets": [_deep_copy_pilot_entry(_PILOT_SUMMARY_FROZEN[k]) for k in keys]}


# ---------------------------------------------------------------------------
# sv_validate_matched_index
# ---------------------------------------------------------------------------


def _viewer_url_matches_path(url_viewer: str, path_repo: str) -> bool:
    """The convention in this repo: url_viewer = '/' + path_repo (or close to it).

    We accept either exact suffix match or strict equality with leading slash.
    """
    if not isinstance(url_viewer, str) or not isinstance(path_repo, str):
        return False
    return url_viewer.lstrip("/") == path_repo.lstrip("/")


def _handle_validate_matched_index(
    args: Mapping[str, Any],
    *,
    _matched_root: Path | None = None,
) -> dict:
    root = _matched_root if _matched_root is not None else (repo_root() / "datasets" / "_matched")
    if not isinstance(root, Path):
        root = Path(root)

    index_path = root / "index.json"
    if not index_path.exists():
        return {"error": f"index.json not found at {index_path}"}

    try:
        index = json.loads(index_path.read_text())
    except json.JSONDecodeError as exc:
        return {"error": f"index.json invalid: {exc}"}

    requested_datasets = args.get("datasets") or []
    requested_set = set(requested_datasets) if requested_datasets else None

    missing_assets: list[dict[str, str]] = []
    orphan_entries: list[dict[str, str]] = []
    counts_by_dataset: dict[str, int] = {}

    file_root = repo_root()

    for entry in index.get("files", []):
        ds = entry.get("dataset", "")
        if requested_set and ds not in requested_set:
            continue
        per_ds_path = root / Path(entry.get("path_repo", "")).name
        if not per_ds_path.exists():
            orphan_entries.append({"dataset": ds, "reason": f"missing per-dataset file: {per_ds_path}"})
            counts_by_dataset[ds] = 0
            continue
        try:
            data = json.loads(per_ds_path.read_text())
        except json.JSONDecodeError as exc:
            orphan_entries.append({"dataset": ds, "reason": f"json decode error: {exc}"})
            counts_by_dataset[ds] = 0
            continue

        ok_count = 0
        for rec in data.get("records", []):
            qid = rec.get("qid", "")
            path_repo = rec.get("path_repo", "")
            url_viewer = rec.get("url_viewer", "")
            if not path_repo:
                orphan_entries.append({"qid": qid, "reason": "missing path_repo"})
                continue
            asset_path = file_root / path_repo
            if not asset_path.exists():
                missing_assets.append({"qid": qid, "path": str(asset_path)})
                continue
            if url_viewer and not _viewer_url_matches_path(url_viewer, path_repo):
                orphan_entries.append({
                    "qid": qid,
                    "reason": f"url_viewer/path_repo mismatch: {url_viewer!r} vs {path_repo!r}",
                })
                continue
            extra = rec.get("extra") or {}
            mask_repo = extra.get("mask_path_repo")
            if mask_repo:
                mask_path = file_root / mask_repo
                if not mask_path.exists():
                    missing_assets.append({"qid": qid, "path": str(mask_path)})
                    continue
            ok_count += 1
        counts_by_dataset[ds] = ok_count

    ok = not missing_assets and not orphan_entries
    return {
        "ok": ok,
        "missing_assets": missing_assets,
        "orphan_entries": orphan_entries,
        "counts_by_dataset": counts_by_dataset,
    }


# ---------------------------------------------------------------------------
# sv_run_v_validation
# ---------------------------------------------------------------------------


def _run_v_validation_default_impl(spec_path: Path, v_levels: list[str]) -> dict:
    """Best-effort wrap of tools/v_validation/.

    The current state of tools/v_validation/ on disk has stale __pycache__/*.pyc
    but no source .py files. We try several import paths; if none resolves we
    return an explicit-but-non-fatal envelope.
    """
    candidates = [
        "tools.v_validation",
        "tools.v_validation.base",
        "tools.v_validation.schema",
    ]

    for mod_name in candidates:
        try:
            mod = importlib.import_module(mod_name)
        except Exception:  # noqa: BLE001
            continue
        for fn_name in ("validate", "run", "main"):
            fn = getattr(mod, fn_name, None)
            if callable(fn):
                try:
                    out = fn(spec_path=str(spec_path), v_levels=list(v_levels))  # type: ignore[call-arg]
                except TypeError:
                    try:
                        out = fn(str(spec_path))
                    except Exception as exc:  # noqa: BLE001
                        return {"ok": False, "failures": [repr(exc)], "warnings": []}
                except Exception as exc:  # noqa: BLE001
                    return {"ok": False, "failures": [repr(exc)], "warnings": []}
                if isinstance(out, Mapping):
                    return {
                        "ok": bool(out.get("ok", False)),
                        "failures": list(out.get("failures", []) or []),
                        "warnings": list(out.get("warnings", []) or []),
                    }
                return {"ok": True, "failures": [], "warnings": [], "result": out}

    return {
        "ok": False,
        "failures": [],
        "warnings": ["tools/v_validation/ has no importable entry point on this machine"],
        "note": "module skeleton exists (only __pycache__ present); rebuild source to enable",
    }


def _handle_run_v_validation(
    args: Mapping[str, Any],
    *,
    _impl: Callable[[Path, list[str]], dict] | None = None,
) -> dict:
    spec_path_arg = args.get("spec_path")
    spec_path = Path(spec_path_arg) if spec_path_arg else (repo_root() / "spec")
    v_levels = list(args.get("v_levels") or ["V1", "V2", "V3", "V4", "V5", "V6"])

    impl = _impl or _run_v_validation_default_impl
    try:
        out = impl(spec_path, v_levels)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"v_validation impl raised: {exc!r}"}

    if not isinstance(out, Mapping):
        return {"ok": False, "error": "v_validation impl did not return a mapping"}
    return {
        "ok": bool(out.get("ok", False)),
        "failures": list(out.get("failures", []) or []),
        "warnings": list(out.get("warnings", []) or []),
        "spec_path": str(spec_path),
        "v_levels": v_levels,
        **{k: v for k, v in out.items() if k not in {"ok", "failures", "warnings"}},
    }


# ---------------------------------------------------------------------------
# Tool specs
# ---------------------------------------------------------------------------


TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="sv_score_astronomy",
        description=(
            "Grade an agent answer for an astronomy V2/V4/V6 question against "
            "ground truth computed from HORIZONS ephemeris."
        ),
        input_schema=_load_schema("sv_score_astronomy"),
        handler=_handle_score_astronomy,
        category="E",
    ),
    ToolSpec(
        name="sv_classify_v",
        description=(
            "Classify a single QA record on V1..V6 + reasoning/knowledge using "
            "the Mindra→Gemini classifier. Mock by default; opt-in with SV_E2E=1."
        ),
        input_schema=_load_schema("sv_classify_v"),
        handler=_handle_classify_v,
        category="E",
    ),
    ToolSpec(
        name="sv_get_v_classify_pilot_summary",
        description=(
            "Return pre-computed V-class distributions for the ScanQA / SQA3D / "
            "M3D-RefSeg pilot (n=100 each). Call before batching."
        ),
        input_schema=_load_schema("sv_get_v_classify_pilot_summary"),
        handler=_handle_pilot_summary,
        category="E",
    ),
    ToolSpec(
        name="sv_validate_matched_index",
        description=(
            "Audit datasets/_matched/*.json: every record's asset path exists on "
            "disk, url_viewer matches, mask assets resolve."
        ),
        input_schema=_load_schema("sv_validate_matched_index"),
        handler=_handle_validate_matched_index,
        category="E",
    ),
    ToolSpec(
        name="sv_run_v_validation",
        description=(
            "Run the tools/v_validation/ suite against the V1..V6 spec; returns "
            "{ok, failures, warnings}."
        ),
        input_schema=_load_schema("sv_run_v_validation"),
        handler=_handle_run_v_validation,
        category="E",
    ),
]
