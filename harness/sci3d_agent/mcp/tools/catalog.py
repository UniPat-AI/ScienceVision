"""Category A — Catalog / Discovery tools (11 read-only JSON tools)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

from sci3d_agent.mcp.registry import ToolSpec, register
from sci3d_agent.mcp.runtime.errors import SVToolError
from sci3d_agent.mcp.runtime.paths import repo_root

_MATCHED_DATASETS: tuple[str, ...] = (
    "scanqa",
    "sqa3d",
    "m3d-refseg",
    "m3d-seg-0005",
    "embodiedscan-demo",
)

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 500


# ---------------------------------------------------------------------------
# Internal JSON loaders (mtime-keyed cache to keep handlers pure & fast)
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> Any:
    """Load JSON file, raising SVToolError if missing or malformed."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError as exc:
        raise SVToolError(f"file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SVToolError(f"invalid JSON at {path}: {exc}") from exc


@lru_cache(maxsize=64)
def _load_json_cached(path_str: str, mtime_ns: int) -> Any:
    return _load_json(Path(path_str))


def _read_json(path: Path) -> Any:
    if not path.exists():
        raise SVToolError(f"file not found: {path}")
    return _load_json_cached(str(path.resolve()), path.stat().st_mtime_ns)


def _matched_index_path() -> Path:
    return repo_root() / "datasets" / "_matched" / "index.json"


def _matched_dataset_path(dataset: str) -> Path:
    return repo_root() / "datasets" / "_matched" / f"{dataset}.json"


def _assets_path() -> Path:
    return repo_root() / "viewer" / "assets.json"


def _datasets_assets_path() -> Path:
    return repo_root() / "viewer" / "datasets_assets.json"


def _negative_assets_path() -> Path:
    return repo_root() / "viewer" / "negative_assets.json"


def _synthetic_negative_assets_path() -> Path:
    return repo_root() / "viewer" / "synthetic_negative_assets.json"


def _astronomy_questions_path() -> Path:
    return repo_root() / "spec" / "astronomy" / "questions.json"


def _load_matched_index() -> dict:
    return _read_json(_matched_index_path())


def _load_dataset_records(dataset: str) -> list[dict]:
    if dataset not in _MATCHED_DATASETS:
        raise SVToolError(
            f"unknown matched dataset: {dataset!r}; expected one of {list(_MATCHED_DATASETS)}"
        )
    payload = _read_json(_matched_dataset_path(dataset))
    records = payload.get("records")
    if not isinstance(records, list):
        raise SVToolError(f"malformed dataset file: {dataset}")
    return records


def _qid_to_dataset(qid: str) -> str | None:
    """Resolve dataset name encoded in qid. Longest prefixes first to avoid m3d-* clash."""
    for ds in sorted(_MATCHED_DATASETS, key=len, reverse=True):
        if qid.startswith(ds + "-"):
            return ds
    return None


# ---------------------------------------------------------------------------
# Common envelope / pagination helpers
# ---------------------------------------------------------------------------


def _clamp_limit(value: Any) -> int:
    if value is None:
        return _DEFAULT_LIMIT
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        return _DEFAULT_LIMIT
    if ivalue < 0:
        return 0
    return min(ivalue, _MAX_LIMIT)


def _clamp_offset(value: Any) -> int:
    if value is None:
        return 0
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, ivalue)


def _paginate(records: list[dict], offset: int, limit: int) -> dict:
    total = len(records)
    page = records[offset : offset + limit]
    return {"records": page, "total": total, "offset": offset, "limit": limit}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


_PAGINATION_PROPS: dict = {
    "limit": {"type": "integer", "minimum": 0, "maximum": _MAX_LIMIT, "default": _DEFAULT_LIMIT},
    "offset": {"type": "integer", "minimum": 0, "default": 0},
}

_SCHEMA_LIST_MATCHED_DATASETS: dict = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

_SCHEMA_SEARCH_MATCHED_QA: dict = {
    "type": "object",
    "properties": {
        "dataset": {"type": "string", "enum": list(_MATCHED_DATASETS)},
        "split": {"type": "string"},
        "scene_or_case_id": {"type": "string"},
        "asset_kind": {"type": "string"},
        "has_mask": {"type": "boolean"},
        "question_contains": {"type": "string"},
        "qid_prefix": {"type": "string"},
        **_PAGINATION_PROPS,
    },
    "additionalProperties": False,
}

_SCHEMA_GET_MATCHED_RECORD: dict = {
    "type": "object",
    "required": ["qid"],
    "properties": {"qid": {"type": "string"}},
    "additionalProperties": False,
}

_SCHEMA_LIST_SCENES: dict = {
    "type": "object",
    "required": ["dataset"],
    "properties": {
        "dataset": {"type": "string", "enum": list(_MATCHED_DATASETS)},
        **_PAGINATION_PROPS,
    },
    "additionalProperties": False,
}

_SCHEMA_LIST_DOMAINS: dict = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

_SCHEMA_LIST_ASSETS: dict = {
    "type": "object",
    "required": ["domain"],
    "properties": {
        "domain": {"type": "string"},
        "format": {"type": "string"},
        "viewer": {"type": "string"},
        **_PAGINATION_PROPS,
    },
    "additionalProperties": False,
}

_SCHEMA_GET_ASSET: dict = {
    "type": "object",
    "required": ["path"],
    "properties": {"path": {"type": "string"}},
    "additionalProperties": False,
}

_SCHEMA_LIST_VQA_DATASETS: dict = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

_SCHEMA_GET_VQA_SAMPLE: dict = {
    "type": "object",
    "required": ["dataset", "sample"],
    "properties": {
        "dataset": {"type": "string"},
        "sample": {"type": ["string", "integer"]},
        "split": {"type": "string"},
    },
    "additionalProperties": False,
}

_SCHEMA_GET_ASTRONOMY_QUESTIONS: dict = {
    "type": "object",
    "required": ["version"],
    "properties": {"version": {"type": "string", "enum": ["V2", "V4", "V6"]}},
    "additionalProperties": False,
}

_SCHEMA_LIST_NEGATIVE_SAMPLES: dict = {
    "type": "object",
    "properties": {
        "domain": {"type": "string"},
        "kind": {"type": "string", "enum": ["natural", "synthetic"]},
        **_PAGINATION_PROPS,
    },
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _handle_list_matched_datasets(args: Mapping[str, Any]) -> dict:
    index = _load_matched_index()
    files = index.get("files", [])
    datasets = [
        {
            "dataset": entry["dataset"],
            "count": entry["count"],
            "path_repo": entry["path_repo"],
        }
        for entry in files
    ]
    return {"datasets": datasets, "total": index.get("total", sum(d["count"] for d in datasets))}


def _filter_matched_records(records: Iterable[dict], args: Mapping[str, Any]) -> list[dict]:
    split = args.get("split")
    scene = args.get("scene_or_case_id")
    asset_kind = args.get("asset_kind")
    has_mask = args.get("has_mask")
    question_contains = args.get("question_contains")
    qid_prefix = args.get("qid_prefix")

    q_lower = question_contains.lower() if isinstance(question_contains, str) else None

    out: list[dict] = []
    for rec in records:
        if split is not None and rec.get("split") != split:
            continue
        if scene is not None and rec.get("scene_or_case_id") != scene:
            continue
        if asset_kind is not None and rec.get("asset_kind") != asset_kind:
            continue
        if qid_prefix is not None and not str(rec.get("qid", "")).startswith(qid_prefix):
            continue
        if q_lower is not None and q_lower not in str(rec.get("question", "")).lower():
            continue
        if has_mask is not None:
            mask_url = rec.get("extra", {}).get("mask_url_viewer") if isinstance(rec.get("extra"), dict) else None
            present = bool(mask_url)
            if bool(has_mask) is not present:
                continue
        out.append(rec)
    return out


def _project_search_record(rec: dict) -> dict:
    extra = rec.get("extra") if isinstance(rec.get("extra"), dict) else {}
    return {
        "qid": rec.get("qid"),
        "dataset": rec.get("dataset"),
        "split": rec.get("split"),
        "scene_or_case_id": rec.get("scene_or_case_id"),
        "question": rec.get("question"),
        "answer": rec.get("answer"),
        "asset_kind": rec.get("asset_kind"),
        "url_viewer": rec.get("url_viewer"),
        "has_mask": bool(extra.get("mask_url_viewer")),
    }


def _handle_search_matched_qa(args: Mapping[str, Any]) -> dict:
    dataset = args.get("dataset")
    datasets = [dataset] if dataset else list(_MATCHED_DATASETS)

    pooled: list[dict] = []
    for ds in datasets:
        pooled.extend(_load_dataset_records(ds))

    filtered = _filter_matched_records(pooled, args)
    projected = [_project_search_record(r) for r in filtered]

    offset = _clamp_offset(args.get("offset"))
    limit = _clamp_limit(args.get("limit"))
    return _paginate(projected, offset, limit)


def _mask_block(rec: dict) -> dict | None:
    extra = rec.get("extra")
    if not isinstance(extra, dict):
        return None
    if not extra.get("mask_url_viewer"):
        return None
    return {
        "mask_path_repo": extra.get("mask_path_repo"),
        "mask_url_viewer": extra.get("mask_url_viewer"),
        "mask_id": extra.get("mask_id"),
        "question_type": extra.get("question_type"),
        "mask_text": extra.get("mask_text"),
        "mask_label_count": extra.get("mask_label_count"),
    }


def _handle_get_matched_record(args: Mapping[str, Any]) -> dict:
    qid = args.get("qid")
    if not isinstance(qid, str) or not qid:
        return {"error": "qid is required and must be a non-empty string"}

    ds_hint = _qid_to_dataset(qid)
    search_order: list[str] = []
    if ds_hint:
        search_order.append(ds_hint)
    for ds in _MATCHED_DATASETS:
        if ds not in search_order:
            search_order.append(ds)

    for ds in search_order:
        try:
            records = _load_dataset_records(ds)
        except SVToolError:
            continue
        for rec in records:
            if rec.get("qid") == qid:
                joined = dict(rec)
                mask = _mask_block(rec)
                if mask is not None:
                    joined["mask"] = mask
                return {"record": joined}

    return {"error": f"qid not found in any matched dataset: {qid}"}


def _handle_list_scenes(args: Mapping[str, Any]) -> dict:
    dataset = args.get("dataset")
    if not isinstance(dataset, str):
        return {"error": "dataset is required"}
    if dataset not in _MATCHED_DATASETS:
        return {"error": f"unknown matched dataset: {dataset}"}

    records = _load_dataset_records(dataset)
    counts: dict[str, int] = {}
    order: list[str] = []
    for rec in records:
        scene = rec.get("scene_or_case_id")
        if scene is None:
            continue
        key = str(scene)
        if key not in counts:
            order.append(key)
        counts[key] = counts.get(key, 0) + 1

    scenes = [{"scene_or_case_id": k, "count": counts[k]} for k in order]
    offset = _clamp_offset(args.get("offset"))
    limit = _clamp_limit(args.get("limit"))
    return _paginate(scenes, offset, limit)


def _handle_list_domains(args: Mapping[str, Any]) -> dict:
    assets = _read_json(_assets_path())
    domains = []
    for key, val in assets.items():
        if isinstance(val, list):
            domains.append({"domain": key, "count": len(val)})
    return {"domains": domains, "total": len(domains)}


def _handle_list_assets(args: Mapping[str, Any]) -> dict:
    domain = args.get("domain")
    if not isinstance(domain, str):
        return {"error": "domain is required"}

    assets = _read_json(_assets_path())
    bucket = assets.get(domain)
    if not isinstance(bucket, list):
        return {"error": f"unknown domain: {domain}"}

    fmt = args.get("format")
    viewer = args.get("viewer")

    filtered: list[dict] = []
    for item in bucket:
        if fmt is not None and item.get("format") != fmt:
            continue
        if viewer is not None and item.get("viewer") != viewer:
            continue
        filtered.append(item)

    offset = _clamp_offset(args.get("offset"))
    limit = _clamp_limit(args.get("limit"))
    return _paginate(filtered, offset, limit)


def _normalize_asset_path(path: str) -> tuple[str, str]:
    stripped = path.lstrip("/")
    with_slash = "/" + stripped
    return stripped, with_slash


def _handle_get_asset(args: Mapping[str, Any]) -> dict:
    path = args.get("path")
    if not isinstance(path, str) or not path:
        return {"error": "path is required"}

    stripped, with_slash = _normalize_asset_path(path)
    assets = _read_json(_assets_path())

    match: dict | None = None
    for domain, items in assets.items():
        if not isinstance(items, list):
            continue
        for item in items:
            item_path = item.get("path")
            if item_path == path or item_path == with_slash or item_path == stripped:
                match = dict(item)
                match.setdefault("domain", domain)
                break
        if match is not None:
            break

    if match is None:
        return {"error": f"asset not found: {path}"}

    on_disk = (repo_root() / stripped).exists()
    match["exists"] = on_disk

    overlay = match.get("overlay")
    if overlay:
        match["siblings"] = [{"kind": "overlay", "path": overlay}]

    return {"record": match}


def _handle_list_vqa_datasets(args: Mapping[str, Any]) -> dict:
    payload = _read_json(_datasets_assets_path())
    datasets = payload.get("datasets")
    if not isinstance(datasets, list):
        return {"error": "datasets_assets.json missing 'datasets' list"}
    return {"datasets": datasets, "total": len(datasets)}


def _vqa_dataset_entry(name: str) -> dict | None:
    payload = _read_json(_datasets_assets_path())
    for entry in payload.get("datasets", []):
        if entry.get("name") == name:
            return entry
    return None


def _read_jsonl(path: Path) -> list[dict]:
    lines: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                lines.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return lines


def _resolve_jsonl_path(jsonl_field: str) -> Path:
    """Map url-style /out_samples/... to repo-root-relative path."""
    rel = jsonl_field.lstrip("/")
    return repo_root() / rel


def _handle_get_vqa_sample(args: Mapping[str, Any]) -> dict:
    dataset = args.get("dataset")
    sample = args.get("sample")
    requested_split = args.get("split")
    if not isinstance(dataset, str):
        return {"error": "dataset is required"}
    if sample is None:
        return {"error": "sample is required"}

    entry = _vqa_dataset_entry(dataset)
    if entry is None:
        return {"error": f"unknown VQA dataset: {dataset}"}

    splits = entry.get("splits") or {}
    if not splits:
        return {"error": f"dataset has no splits: {dataset}"}

    split_names = [requested_split] if requested_split else list(splits.keys())

    for split_name in split_names:
        split_meta = splits.get(split_name)
        if not isinstance(split_meta, dict):
            continue
        jsonl_field = split_meta.get("jsonl")
        if not isinstance(jsonl_field, str):
            continue
        jsonl_path = _resolve_jsonl_path(jsonl_field)
        if not jsonl_path.exists():
            continue
        rows = _read_jsonl(jsonl_path)
        record: dict | None = None
        if isinstance(sample, int):
            if 0 <= sample < len(rows):
                record = rows[sample]
        else:
            sample_str = str(sample)
            for row in rows:
                if row.get("id") == sample_str or row.get("qid") == sample_str:
                    record = row
                    break
            if record is None:
                try:
                    idx = int(sample_str)
                except ValueError:
                    idx = -1
                if 0 <= idx < len(rows):
                    record = rows[idx]
        if record is not None:
            out = dict(record)
            out["split"] = split_name
            out["dataset"] = dataset
            return {"record": out}

    return {"error": f"sample not found in {dataset}: {sample!r}"}


def _handle_get_astronomy_questions(args: Mapping[str, Any]) -> dict:
    version = args.get("version")
    if version not in ("V2", "V4", "V6"):
        return {"error": "version must be one of V2|V4|V6"}

    path = _astronomy_questions_path()
    if not path.exists():
        raise SVToolError(f"astronomy spec not found at {path}")

    payload = _read_json(path)
    questions = payload.get("questions")
    if not isinstance(questions, list):
        raise SVToolError(
            f"astronomy spec has unexpected shape (no 'questions' list) at {path}"
        )

    filtered = [q for q in questions if q.get("v_class") == version]
    return {"version": version, "questions": filtered, "total": len(filtered)}


def _project_negative_records(raw: dict, kind: str, domain_filter: str | None) -> list[dict]:
    out: list[dict] = []
    for domain, items in raw.items():
        if not isinstance(items, list):
            continue
        if domain_filter is not None and domain != domain_filter:
            continue
        for item in items:
            rec = dict(item)
            rec["domain"] = domain
            rec["kind"] = kind
            out.append(rec)
    return out


def _handle_list_negative_samples(args: Mapping[str, Any]) -> dict:
    domain = args.get("domain")
    kind = args.get("kind")

    domain_filter = domain if isinstance(domain, str) else None

    pooled: list[dict] = []
    if kind in (None, "natural"):
        path = _negative_assets_path()
        if path.exists():
            raw = _read_json(path)
            if isinstance(raw, dict):
                pooled.extend(_project_negative_records(raw, "natural", domain_filter))
    if kind in (None, "synthetic"):
        path = _synthetic_negative_assets_path()
        if path.exists():
            raw = _read_json(path)
            if isinstance(raw, dict):
                pooled.extend(_project_negative_records(raw, "synthetic", domain_filter))

    offset = _clamp_offset(args.get("offset"))
    limit = _clamp_limit(args.get("limit"))
    return _paginate(pooled, offset, limit)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


TOOLS: list[ToolSpec] = [
    register(
        ToolSpec(
            name="sv_list_matched_datasets",
            description="List the 5 tier-1 matched 3D-grounded QA whitelist datasets + per-dataset counts.",
            input_schema=_SCHEMA_LIST_MATCHED_DATASETS,
            handler=_handle_list_matched_datasets,
            category="A",
        )
    ),
    register(
        ToolSpec(
            name="sv_search_matched_qa",
            description="Filterable browse over tier-1 matched QA records (dataset / split / scene / mask / question substring).",
            input_schema=_SCHEMA_SEARCH_MATCHED_QA,
            handler=_handle_search_matched_qa,
            category="A",
        )
    ),
    register(
        ToolSpec(
            name="sv_get_matched_record",
            description="Fetch a full matched QA record by qid, searching all 5 datasets; joins mask metadata when present.",
            input_schema=_SCHEMA_GET_MATCHED_RECORD,
            handler=_handle_get_matched_record,
            category="A",
        )
    ),
    register(
        ToolSpec(
            name="sv_list_scenes",
            description="List distinct scene_or_case_id values + per-scene record counts for a tier-1 dataset.",
            input_schema=_SCHEMA_LIST_SCENES,
            handler=_handle_list_scenes,
            category="A",
        )
    ),
    register(
        ToolSpec(
            name="sv_list_domains",
            description="List all asset domains from viewer/assets.json with counts.",
            input_schema=_SCHEMA_LIST_DOMAINS,
            handler=_handle_list_domains,
            category="A",
        )
    ),
    register(
        ToolSpec(
            name="sv_list_assets",
            description="List assets within a single domain, optionally filtered by format and viewer.",
            input_schema=_SCHEMA_LIST_ASSETS,
            handler=_handle_list_assets,
            category="A",
        )
    ),
    register(
        ToolSpec(
            name="sv_get_asset",
            description="Fetch a single asset record by path; reports on-disk existence and overlay siblings.",
            input_schema=_SCHEMA_GET_ASSET,
            handler=_handle_get_asset,
            category="A",
        )
    ),
    register(
        ToolSpec(
            name="sv_list_vqa_datasets",
            description="List all VQA datasets registered in viewer/datasets_assets.json.",
            input_schema=_SCHEMA_LIST_VQA_DATASETS,
            handler=_handle_list_vqa_datasets,
            category="A",
        )
    ),
    register(
        ToolSpec(
            name="sv_get_vqa_sample",
            description="Look up one VQA sample by id or integer index within a dataset (across all splits unless 'split' is given).",
            input_schema=_SCHEMA_GET_VQA_SAMPLE,
            handler=_handle_get_vqa_sample,
            category="A",
        )
    ),
    register(
        ToolSpec(
            name="sv_get_astronomy_questions",
            description="Return astronomy questions for one V-tier (V2|V4|V6) from spec/astronomy/questions.json.",
            input_schema=_SCHEMA_GET_ASTRONOMY_QUESTIONS,
            handler=_handle_get_astronomy_questions,
            category="A",
        )
    ),
    register(
        ToolSpec(
            name="sv_list_negative_samples",
            description="List negative samples (union of natural + synthetic banks) with optional domain/kind filters.",
            input_schema=_SCHEMA_LIST_NEGATIVE_SAMPLES,
            handler=_handle_list_negative_samples,
            category="A",
        )
    ),
]
