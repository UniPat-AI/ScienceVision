"""Fail-closed route contracts for the ``cc`` and ``ccmcp`` baselines.

The two routes are intentionally different experimental conditions, not just
different labels on one container image:

* ``cc`` must have no ScienceVision MCP server or MCP-shaped tool surface.
* ``ccmcp`` must record the *actual* server surface it exposed.  A task that
  says it required 73 tools but ran against 53 is an infrastructure-invalid
  trial, not a zero-credit model answer.

This module deliberately lives under :mod:`harbor_adapters.sci3d`, rather than
only under ``scripts/harbor``.  The containerized scorer copies this package
into its image whereas it does not copy the launcher scripts.  The thin
``scripts.harbor.route_contracts`` module re-exports this public API for task
generators and launchers.

Everything here is JSON-only and dependency-free so a receipt can be written by
either the stdio Codex route or the optional HTTP worker without importing
Harbor, FastMCP, or a tool handler.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping, TypeAlias


ROUTE_CONTRACT_SCHEMA_VERSION = "sciencevision.route_contract.v1"
MCP_HEALTH_SCHEMA_VERSION = "sciencevision.mcp_health.v1"
MCP_HEALTH_FILENAME = "mcp_health.json"
MCP_SERVER_NAME = "sciencevision"
MCP_SERVICE_NAME = "sciencevision-mcp"
MCP_HTTP_URL = "http://127.0.0.1:8765/mcp"
MCP_HTTP_HEALTH_PATH = "/run/sciencevision-mcp/health.json"

# A dict is intentional: downstream score JSON, existing reporters, and shell
# launchers all consume JSON mappings.  Keeping the public result mapping-shaped
# avoids a second serializer that could lose a reason code.
HealthVerdict: TypeAlias = dict[str, Any]


class RouteContractError(ValueError):
    """A launcher supplied a structurally ambiguous route contract."""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise RouteContractError("route contract values must be JSON serializable") from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _as_iterable(value: Iterable[Any] | Any | None) -> Iterable[Any]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        return (value,)
    try:
        return iter(value)
    except TypeError as exc:
        raise RouteContractError("expected an iterable") from exc


def bare_tool_name(value: Any) -> str:
    """Normalize a sciencevision wire name without treating arbitrary text as one.

    ``mcp__sciencevision__sv3d_render`` and ``sv3d_render`` identify the same
    catalog entry.  Other ``mcp__<server>__`` names are left as their bare tool
    suffix because receipts compare the registered server separately.
    """
    text = str(value or "").strip()
    if text.startswith("mcp__") and "__" in text[5:]:
        return text.split("__", 2)[-1]
    return text


def qualified_tool_name(value: Any) -> str:
    """Return the canonical Codex MCP wire spelling for a tool name."""
    bare = bare_tool_name(value)
    return f"mcp__{MCP_SERVER_NAME}__{bare}" if bare else ""


def _unique_tools(values: Iterable[Any] | Any | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in _as_iterable(values):
        name = bare_tool_name(value)
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _groups(values: Iterable[Iterable[Any]] | Any | None) -> list[list[str]]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)):
        raise RouteContractError("required_tool_groups must be groups, not one string")
    out: list[list[str]] = []
    for group in values:
        names = _unique_tools(group)
        if not names:
            raise RouteContractError("a required tool group cannot be empty")
        out.append(names)
    return out


def _normal_route(route: Any) -> str:
    raw = str(route or "").strip().lower()
    aliases = {
        "cc": "cc",
        "codex": "cc",
        "ccmcp": "ccmcp",
        "cc-mcp": "ccmcp",
        "codex-mcp": "ccmcp",
    }
    try:
        return aliases[raw]
    except KeyError as exc:
        raise RouteContractError(f"unknown route {route!r}; expected cc or ccmcp") from exc


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _tool_entries_from_catalog(catalog: Mapping[str, Any]) -> list[dict[str, str]]:
    """Make a stable ``[{name, schema_sha256}]`` manifest from a surface JSON."""
    raw_entries = catalog.get("tools")
    entries: list[dict[str, str]] = []
    if isinstance(raw_entries, list):
        for raw in raw_entries:
            if isinstance(raw, Mapping):
                name = bare_tool_name(raw.get("name"))
                schema_hash = _optional_text(
                    raw.get("schema_sha256", raw.get("input_schema_sha256"))
                )
            else:
                name = bare_tool_name(raw)
                schema_hash = None
            if not name:
                raise RouteContractError("catalog contains a tool without a name")
            entry = {"name": name}
            if schema_hash is not None:
                entry["schema_sha256"] = schema_hash
            entries.append(entry)
    else:
        for raw in catalog.get("tool_names") or ():
            name = bare_tool_name(raw)
            if not name:
                raise RouteContractError("catalog contains an empty tool name")
            entries.append({"name": name})

    names = [entry["name"] for entry in entries]
    if len(names) != len(set(names)):
        raise RouteContractError("catalog contains duplicate tool names")
    return entries


def canonical_catalog(catalog: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Normalize a live server surface for embedding in a route contract.

    A catalog carries two independent fingerprints: names (catalog identity)
    and names+input schema (call-shape identity).  Both are checked at score
    time.  The hashes are recomputed when omitted, but a supplied hash is kept
    so :func:`validate_health_receipt` can detect an internally inconsistent
    external worker receipt rather than blessing it by recomputing over a
    different payload.
    """
    if catalog is None:
        return None
    if not isinstance(catalog, Mapping):
        raise RouteContractError("catalog must be a mapping emitted by --surface")

    entries = _tool_entries_from_catalog(catalog)
    names = [entry["name"] for entry in entries]
    schema_entries = [
        {"name": entry["name"], "schema_sha256": entry.get("schema_sha256", "")}
        for entry in entries
    ]
    schemas_complete = all("schema_sha256" in entry for entry in entries)
    computed_catalog = _sha256(names)
    computed_schema = _sha256(schema_entries) if schemas_complete else None
    catalog_hash = _optional_text(catalog.get("catalog_sha256")) or computed_catalog
    schema_hash = _optional_text(catalog.get("schema_sha256")) or computed_schema
    if schema_hash is None:
        raise RouteContractError("catalog must provide schema_sha256 or per-tool schema hashes")
    viewer_family = _optional_text(catalog.get("viewer_family"))
    return {
        "tool_names": names,
        "tools": entries,
        "tool_count": len(names),
        "catalog_sha256": catalog_hash,
        "schema_sha256": schema_hash,
        "viewer_family": viewer_family,
        # The recomputed values let validation prove that a receipt did not
        # simply assert an expected fingerprint alongside another manifest.
        "computed_catalog_sha256": computed_catalog,
        "computed_schema_sha256": computed_schema,
        "schema_manifest_complete": schemas_complete,
    }


def _contract_hash_payload(contract: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in contract.items() if key != "contract_sha256"}


def contract_sha256(contract: Mapping[str, Any]) -> str:
    """Fingerprint a contract excluding its self-referential hash field."""
    if not isinstance(contract, Mapping):
        raise RouteContractError("contract must be a mapping")
    return _sha256(_contract_hash_payload(contract))


def build_route_contract(
    route: str,
    *,
    viewer_family: str | None = None,
    allowed_tools: Iterable[str] = (),
    required_tool_groups: Iterable[Iterable[str]] = (),
    min_successful_calls: int = 0,
    render_required: bool = False,
    catalog: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the immutable task-side contract for a CC-family route.

    ``allowed_tools`` is the task-specific MCP evidence surface, not the
    legacy ``task.toml`` generic-tool allowlist.  Supplying no task-specific
    requirements leaves a legacy ``ccmcp`` task eligible but does not claim
    that it exercised MCP.  New CCMCP tasks should supply a required group
    and/or a positive minimum call count.
    """
    normalized_route = _normal_route(route)
    if isinstance(min_successful_calls, bool) or not isinstance(min_successful_calls, int):
        raise RouteContractError("min_successful_calls must be a non-negative integer")
    if min_successful_calls < 0:
        raise RouteContractError("min_successful_calls must be non-negative")

    if normalized_route == "cc":
        # Deliberately do not carry caller-provided MCP state forward.  The
        # receipt validator enforces this negative guarantee again against the
        # actual runtime, so a caller cannot turn CC into a hidden MCP route by
        # merely adding a metadata field.
        mcp: dict[str, Any] = {
            "server": MCP_SERVER_NAME,
            "exposed": False,
            "required": False,
            "viewer_family": None,
            "allowed_tools": [],
            "required_tool_groups": [],
            "min_successful_calls": 0,
            "render_required": False,
            "catalog": None,
        }
    else:
        tools = _unique_tools(allowed_tools)
        groups = _groups(required_tool_groups)
        missing = sorted({name for group in groups for name in group} - set(tools))
        if tools and missing:
            raise RouteContractError(
                "required tool groups name tools outside allowed_tools: " + ", ".join(missing)
            )
        requires_evidence = bool(groups or min_successful_calls > 0 or render_required)
        minimum = max(min_successful_calls, 1 if render_required else 0)
        mcp = {
            "server": MCP_SERVER_NAME,
            "exposed": True,
            "required": requires_evidence,
            "viewer_family": _optional_text(viewer_family),
            "allowed_tools": tools,
            "required_tool_groups": groups,
            "min_successful_calls": minimum,
            "render_required": bool(render_required),
            "catalog": canonical_catalog(catalog),
        }

    contract: dict[str, Any] = {
        "schema_version": ROUTE_CONTRACT_SCHEMA_VERSION,
        "route": normalized_route,
        "mcp": mcp,
    }
    contract["contract_sha256"] = contract_sha256(contract)
    return contract


def canonicalize_route_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild a route contract after a lossy configuration serialization.

    TOML has no null literal.  The task generator consequently represents
    optional public metadata as ``""`` while its verifier-only JSON keeps
    Python ``None``.  A launcher must normalize that transport sentinel before
    it writes a receipt hash, otherwise a healthy CC trial is falsely reported
    as ``route_contract_mismatch``.  The supplied self-hash remains a tamper
    check: a value other than the rebuilt canonical hash is rejected.
    """
    if not isinstance(contract, Mapping):
        raise RouteContractError("route_contract must be a mapping")
    mcp = contract.get("mcp")
    if not isinstance(mcp, Mapping):
        raise RouteContractError("route_contract has no mcp mapping")

    raw_catalog = mcp.get("catalog")
    if raw_catalog in (None, ""):
        catalog = None
    elif isinstance(raw_catalog, Mapping):
        catalog = raw_catalog
    else:
        raise RouteContractError("route_contract mcp.catalog must be a mapping or absent")

    normalized = build_route_contract(
        str(contract.get("route") or ""),
        viewer_family=_optional_text(mcp.get("viewer_family")),
        allowed_tools=mcp.get("allowed_tools") or (),
        required_tool_groups=mcp.get("required_tool_groups") or (),
        min_successful_calls=mcp.get("min_successful_calls", 0),
        render_required=bool(mcp.get("render_required", False)),
        catalog=catalog,
    )
    supplied_hash = _optional_text(contract.get("contract_sha256"))
    if supplied_hash is not None and supplied_hash != normalized["contract_sha256"]:
        raise RouteContractError("route_contract self-hash disagrees with its canonical payload")
    return normalized


def _require_contract(contract: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    if not isinstance(contract, Mapping):
        raise RouteContractError("route_contract must be a mapping")
    route = _normal_route(contract.get("route"))
    mcp = contract.get("mcp")
    if not isinstance(mcp, Mapping):
        raise RouteContractError("route_contract.mcp must be a mapping")
    if contract.get("schema_version") not in (None, ROUTE_CONTRACT_SCHEMA_VERSION):
        raise RouteContractError("unsupported route contract schema version")
    return route, mcp


def route_system_context(contract: Mapping[str, Any]) -> str:
    """Route-specific context appended to the agent system prompt.

    It contains operational constraints only: no task answer, scorer details,
    path to a ground-truth file, or other task-specific privileged information.
    """
    route, mcp = _require_contract(contract)
    if route == "cc":
        return (
            "\n\n[Route contract: CC]\n"
            "This is the no-MCP control route. No ScienceVision MCP server is registered "
            "and no `mcp__sciencevision__*` tool is available. Do not invent, request, "
            "or claim a ScienceVision MCP call; use only the tools actually registered in "
            "this run.\n"
        )

    allowed = [qualified_tool_name(name) for name in mcp.get("allowed_tools") or ()]
    required = bool(mcp.get("required"))
    groups = mcp.get("required_tool_groups") or []
    minimum = int(mcp.get("min_successful_calls") or 0)
    viewer = _optional_text(mcp.get("viewer_family"))
    lines = [
        "",
        "[Route contract: CC-MCP]",
        "ScienceVision MCP is registered for this run. The live registered tool catalog and "
        "its schemas are authoritative; do not invent tool names or arguments.",
    ]
    if viewer:
        lines.append(f"The task is gated to viewer family `{viewer}`.")
    if allowed:
        lines.append("Task-eligible MCP tools: " + ", ".join(f"`{name}`" for name in allowed) + ".")
    if required:
        lines.append(
            f"You MUST obtain at least {minimum} successful ScienceVision MCP call(s) before "
            "finalizing the answer."
        )
        if groups:
            rendered = ["/".join(qualified_tool_name(name) for name in group) for group in groups]
            lines.append(
                "Each required evidence group needs one successful call: "
                + "; ".join(rendered)
                + "."
            )
        if mcp.get("render_required"):
            lines.append("A renderer result marked `renderer_skipped` is not a successful render.")
        lines.append(
            "An error, timeout, empty result, or skipped renderer does not satisfy this contract."
        )
    else:
        lines.append("This legacy task has no mandatory MCP evidence threshold.")
    return "\n".join(lines) + "\n"


def _schema_hash_map(value: Mapping[str, Any] | Iterable[Any] | None) -> dict[str, str]:
    """Accept either `{name: hash/schema}` or `[{name, schema...}]` evidence."""
    if value is None:
        return {}
    items = value.items() if isinstance(value, Mapping) else _as_iterable(value)
    out: dict[str, str] = {}
    if isinstance(value, Mapping):
        iterator = items
        for raw_name, raw_schema in iterator:
            name = bare_tool_name(raw_name)
            if not name:
                continue
            if isinstance(raw_schema, str):
                out[name] = raw_schema
            else:
                out[name] = _sha256(raw_schema)
        return out

    for row in items:
        if not isinstance(row, Mapping):
            continue
        name = bare_tool_name(row.get("name"))
        if not name:
            continue
        claimed = _optional_text(row.get("schema_sha256", row.get("input_schema_sha256")))
        if claimed is not None:
            out[name] = claimed
        elif "input_schema" in row:
            out[name] = _sha256(row["input_schema"])
    return out


def _normalize_servers(values: Iterable[Any] | Any | None) -> list[str]:
    out: list[str] = []
    for value in _as_iterable(values):
        if isinstance(value, Mapping):
            name = _optional_text(value.get("name"))
        else:
            name = _optional_text(value)
        if name and name not in out:
            out.append(name)
    return out


def _receipt_surface(
    server_surface: Mapping[str, Any] | None,
    worker_health: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    if isinstance(server_surface, Mapping):
        return server_surface
    if isinstance(worker_health, Mapping):
        nested = worker_health.get("server_surface", worker_health.get("surface"))
        if isinstance(nested, Mapping):
            return nested
        # The HTTP worker's health file may itself be the compact surface
        # receipt (tool_names + catalog/schema fingerprints), rather than wrap
        # a duplicate `server_surface` object.  Accept that canonical C-group
        # shape; validation still compares its hashes to the task contract.
        if (
            isinstance(worker_health.get("tools"), list)
            or isinstance(worker_health.get("tool_names"), list)
        ):
            return worker_health
    return None


def write_health_receipt(
    agent_dir: Path | str,
    contract: Mapping[str, Any],
    *,
    exposed_tools: Iterable[str] | None = None,
    server_surface: Mapping[str, Any] | None = None,
    exposed_schemas: Mapping[str, Any] | Iterable[Any] | None = None,
    registered_servers: Iterable[str] | None = None,
    service: str | None = None,
    service_url: str | None = None,
    service_health_path: str | None = None,
    worker_health: Mapping[str, Any] | None = None,
    runtime: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write an atomic agent-side MCP health receipt.

    The stdio runner can provide just ``registered_servers``, ``exposed_tools``
    and ``server_surface``.  The HTTP worker uses the same envelope plus the C
    group convention: ``service=sciencevision-mcp``, its loopback MCP URL, and
    ``/run/sciencevision-mcp/health.json``.  No defaults claim that a server was
    registered or healthy; omitted evidence remains visible and fails closed at
    validation time.
    """
    route, _ = _require_contract(contract)
    worker = dict(worker_health) if isinstance(worker_health, Mapping) else None
    surface = _receipt_surface(server_surface, worker)
    if exposed_tools is None and isinstance(surface, Mapping):
        exposed_tools = surface.get("tool_names") or [
            row.get("name") for row in surface.get("tools", []) if isinstance(row, Mapping)
        ]
    if exposed_schemas is None and isinstance(surface, Mapping):
        exposed_schemas = surface.get("tools") if isinstance(surface.get("tools"), list) else None

    # Worker health files usually own these values.  Preserve explicit runner
    # arguments as the higher-precedence observation so a stale worker file
    # cannot overwrite the launch record.
    if worker:
        worker_schema = _optional_text(worker.get("schema_version")) or ""
        # C's worker health file is intentionally small and identifies itself
        # by schema_version rather than repeating a service label on every
        # update.  Its public convention is still the sciencevision-mcp
        # service, so normalize that shape here.
        inferred_service = (
            MCP_SERVICE_NAME if worker_schema.startswith("sciencevision.mcp_worker_health.") else None
        )
        service = service or _optional_text(worker.get("service")) or inferred_service
        service_url = service_url or _optional_text(
            worker.get("service_url", worker.get("url", worker.get("http_surface")))
        )
        service_health_path = service_health_path or _optional_text(
            worker.get("service_health_path", worker.get("health_path"))
        )
        if service_health_path is None and inferred_service is not None:
            service_health_path = MCP_HTTP_HEALTH_PATH

    payload: dict[str, Any] = {
        "schema_version": MCP_HEALTH_SCHEMA_VERSION,
        "route": route,
        "route_contract_sha256": contract_sha256(contract),
        "registered_servers": _normalize_servers(registered_servers),
        "exposed_tools": _unique_tools(exposed_tools),
        "exposed_schema_sha256": _schema_hash_map(exposed_schemas),
        "server_surface": dict(surface) if isinstance(surface, Mapping) else None,
        "service": _optional_text(service),
        "service_url": _optional_text(service_url),
        "service_health_path": _optional_text(service_health_path),
        "worker_health": worker,
        "runtime": dict(runtime) if isinstance(runtime, Mapping) else {},
    }
    path = Path(agent_dir) / MCP_HEALTH_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    # A scorer must never see a partially-written health record.  The temp file
    # stays in the same directory so replace is atomic on the mounted volume.
    fd, tmp_name = tempfile.mkstemp(prefix=f".{MCP_HEALTH_FILENAME}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return payload


def load_health_receipt(agent_dir: Path | str) -> dict[str, Any] | None:
    """Read one health receipt, returning ``None`` for a missing/bad record."""
    try:
        payload = json.loads((Path(agent_dir) / MCP_HEALTH_FILENAME).read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _record_reason(reasons: list[str], code: str) -> None:
    if code not in reasons:
        reasons.append(code)


def _is_mcp_shaped_name(name: str) -> bool:
    return name.startswith("mcp__") or bare_tool_name(name).startswith(("sv3d_", "sv_"))


def _surface_issues(
    surface: Mapping[str, Any] | None,
    expected: Mapping[str, Any] | None,
    expected_viewer: str | None,
    exposed_tools: list[str],
    exposed_schemas: Mapping[str, str],
) -> list[str]:
    """Compare runtime server surface against contract/client evidence."""
    reasons: list[str] = []
    if not isinstance(surface, Mapping):
        return ["missing_server_surface"]
    try:
        actual = canonical_catalog(surface)
    except RouteContractError:
        return ["malformed_server_surface"]
    assert actual is not None

    # ``canonical_catalog`` retains claimed values and recomputes both.  A
    # mismatch here is a corrupt or stale worker record even before comparing to
    # the task contract.
    if actual["catalog_sha256"] != actual["computed_catalog_sha256"]:
        _record_reason(reasons, "invalid_server_catalog_hash")
    if (
        actual["computed_schema_sha256"] is not None
        and actual["schema_sha256"] != actual["computed_schema_sha256"]
    ):
        _record_reason(reasons, "invalid_server_schema_hash")
    declared_count = surface.get("tool_count")
    if declared_count is not None and declared_count != actual["tool_count"]:
        _record_reason(reasons, "invalid_server_tool_count")
    if expected_viewer != actual["viewer_family"]:
        _record_reason(reasons, "viewer_family_mismatch")

    actual_names = actual["tool_names"]
    if exposed_tools and set(exposed_tools) != set(actual_names):
        _record_reason(reasons, "catalog_mismatch")
    if not exposed_tools:
        _record_reason(reasons, "missing_exposed_tool_catalog")

    if exposed_schemas:
        expected_by_name = {
            row["name"]: row.get("schema_sha256", "") for row in actual["tools"]
        }
        if set(exposed_schemas) != set(expected_by_name) or any(
            exposed_schemas.get(name) != schema for name, schema in expected_by_name.items()
        ):
            _record_reason(reasons, "schema_mismatch")

    if expected is None:
        _record_reason(reasons, "missing_contract_catalog")
        return reasons
    try:
        wanted = canonical_catalog(expected)
    except RouteContractError:
        _record_reason(reasons, "malformed_contract_catalog")
        return reasons
    assert wanted is not None
    if wanted["catalog_sha256"] != wanted["computed_catalog_sha256"]:
        _record_reason(reasons, "invalid_contract_catalog_hash")
    if (
        wanted["computed_schema_sha256"] is not None
        and wanted["schema_sha256"] != wanted["computed_schema_sha256"]
    ):
        _record_reason(reasons, "invalid_contract_schema_hash")
    if wanted["tool_names"] != actual_names:
        _record_reason(reasons, "catalog_mismatch")
    if wanted["catalog_sha256"] != actual["catalog_sha256"]:
        _record_reason(reasons, "catalog_mismatch")
    if wanted["schema_sha256"] != actual["schema_sha256"]:
        _record_reason(reasons, "schema_mismatch")
    return reasons


def _worker_health_issues(receipt: Mapping[str, Any]) -> list[str]:
    """Validate C group's optional HTTP-worker evidence without requiring HTTP."""
    reasons: list[str] = []
    service = _optional_text(receipt.get("service"))
    url = _optional_text(receipt.get("service_url"))
    health_path = _optional_text(receipt.get("service_health_path"))
    worker = receipt.get("worker_health")
    http_evidence = (
        service is not None or url is not None or health_path is not None or worker is not None
    )
    if not http_evidence:
        return reasons
    if service != MCP_SERVICE_NAME:
        _record_reason(reasons, "worker_service_mismatch")
    # The endpoint is deliberately fixed for the worker deployment.  A unit
    # test may omit it (stdio); if it supplies it, a different endpoint is a
    # non-comparable service, not a harmless annotation.
    if url is not None and url != MCP_HTTP_URL:
        _record_reason(reasons, "worker_url_mismatch")
    if health_path is not None and health_path != MCP_HTTP_HEALTH_PATH:
        _record_reason(reasons, "worker_health_path_mismatch")
    if worker is not None:
        if not isinstance(worker, Mapping):
            _record_reason(reasons, "malformed_worker_health")
        else:
            status = _optional_text(worker.get("status", worker.get("state")))
            if status is not None and status.lower() not in {"ok", "healthy", "ready", "running"}:
                _record_reason(reasons, "worker_unhealthy")
    return reasons


def validate_health_receipt(
    receipt: Mapping[str, Any] | None,
    contract: Mapping[str, Any],
) -> HealthVerdict:
    """Validate route isolation and catalog identity, fail-closed.

    A returned ``infra_invalid`` means the trial should be excluded from a
    route comparison.  It is intentionally distinct from
    ``agent_noncompliant``, which is decided later from the agent trajectory.
    """
    try:
        route, mcp = _require_contract(contract)
    except RouteContractError as exc:
        return {
            "status": "infra_invalid",
            "reason_codes": ["malformed_route_contract"],
            "detail": str(exc),
        }
    if not isinstance(receipt, Mapping):
        return {"status": "infra_invalid", "reason_codes": ["missing_health_receipt"]}

    reasons: list[str] = []
    if receipt.get("schema_version") not in (None, MCP_HEALTH_SCHEMA_VERSION):
        _record_reason(reasons, "unsupported_health_receipt_schema")
    try:
        receipt_route = _normal_route(receipt.get("route"))
    except RouteContractError:
        receipt_route = None
    if receipt_route != route:
        _record_reason(reasons, "route_mismatch")
    try:
        expected_contract_hash = contract_sha256(contract)
    except RouteContractError:
        expected_contract_hash = None
    if _optional_text(receipt.get("route_contract_sha256")) != expected_contract_hash:
        _record_reason(reasons, "route_contract_mismatch")

    servers = _normalize_servers(receipt.get("registered_servers"))
    exposed = _unique_tools(receipt.get("exposed_tools"))
    exposed_schemas = _schema_hash_map(receipt.get("exposed_schema_sha256"))
    surface = receipt.get("server_surface")

    if route == "cc":
        if servers:
            _record_reason(reasons, "cc_mcp_registered")
        if any(_is_mcp_shaped_name(name) for name in exposed):
            _record_reason(reasons, "cc_mcp_exposed")
        if surface is not None:
            _record_reason(reasons, "cc_mcp_surface_present")
        # A HTTP worker in a CC trial is itself an MCP visibility leak.
        if any(
            receipt.get(key) is not None
            for key in ("service", "service_url", "service_health_path")
        ):
            _record_reason(reasons, "cc_mcp_worker_present")
    else:
        if servers != [str(mcp.get("server") or MCP_SERVER_NAME)]:
            _record_reason(reasons, "mcp_server_registration_mismatch")
        expected_catalog = mcp.get("catalog") if isinstance(mcp.get("catalog"), Mapping) else None
        reasons.extend(
            code
            for code in _surface_issues(
                surface if isinstance(surface, Mapping) else None,
                expected_catalog,
                _optional_text(mcp.get("viewer_family")),
                exposed,
                exposed_schemas,
            )
            if code not in reasons
        )
        for code in _worker_health_issues(receipt):
            _record_reason(reasons, code)
        worker = receipt.get("worker_health")
        if isinstance(worker, Mapping):
            worker_viewer = _optional_text(worker.get("viewer_family"))
            expected_viewer = _optional_text(mcp.get("viewer_family"))
            if worker_viewer is not None and worker_viewer != expected_viewer:
                _record_reason(reasons, "worker_viewer_family_mismatch")
            worker_catalog = _optional_text(worker.get("catalog_sha256"))
            if (
                worker_catalog is not None
                and expected_catalog is not None
                and worker_catalog != expected_catalog.get("catalog_sha256")
            ):
                _record_reason(reasons, "worker_catalog_hash_mismatch")
            worker_schema = _optional_text(worker.get("schema_sha256"))
            if (
                worker_schema is not None
                and expected_catalog is not None
                and worker_schema != expected_catalog.get("schema_sha256")
            ):
                _record_reason(reasons, "worker_schema_hash_mismatch")

    return {
        "status": "eligible" if not reasons else "infra_invalid",
        "reason_codes": reasons,
        "route": route,
        "registered_servers": servers,
        "exposed_tool_count": len(exposed),
    }


# Short alias used by callers that frame the check as a health decision rather
# than a receipt validation.  Retained alongside the explicit name above.
evaluate_health_receipt = validate_health_receipt


def _successful_mcp_calls(calls: Iterable[Any], allowed: set[str]) -> list[dict[str, Any]]:
    successes: list[dict[str, Any]] = []
    for raw in calls:
        if not isinstance(raw, Mapping):
            continue
        name = bare_tool_name(raw.get("name"))
        raw_name = str(raw.get("name") or "")
        if not name or not _is_mcp_shaped_name(raw_name):
            continue
        if allowed and name not in allowed:
            continue
        if str(raw.get("outcome") or "").lower() != "ok":
            continue
        successes.append(dict(raw, name=name))
    return successes


def _is_render_tool(name: str) -> bool:
    bare = bare_tool_name(name)
    return bare in {"sv3d_render", "sv3d_render_canonical_views"} or bare.startswith("sv_render_")


def evaluate_route_eligibility(
    contract: Mapping[str, Any],
    receipt: Mapping[str, Any] | None,
    calls: Iterable[Any] | None,
) -> HealthVerdict:
    """Combine runtime health with trajectory evidence.

    Ordering is meaningful: infrastructure ambiguity wins over model behavior.
    A broken catalog cannot be relabeled as a model refusal merely because the
    model did not call it.
    """
    health = validate_health_receipt(receipt, contract)
    if health["status"] != "eligible":
        return health
    route, mcp = _require_contract(contract)
    all_calls = list(calls or ())
    if route == "cc":
        if any(
            isinstance(call, Mapping) and _is_mcp_shaped_name(str(call.get("name") or ""))
            for call in all_calls
        ):
            return {
                "status": "infra_invalid",
                "reason_codes": ["cc_mcp_call_observed"],
                "route": route,
            }
        return {**health, "successful_mcp_calls": 0, "successful_tools": []}

    allowed = set(_unique_tools(mcp.get("allowed_tools")))
    successes = _successful_mcp_calls(all_calls, allowed)
    if not bool(mcp.get("required")):
        return {
            **health,
            "successful_mcp_calls": len(successes),
            "successful_tools": [call["name"] for call in successes],
            "evidence_required": False,
        }

    reasons: list[str] = []
    minimum = int(mcp.get("min_successful_calls") or 0)
    if len(successes) < minimum:
        _record_reason(reasons, "insufficient_successful_mcp_calls")
    names = {call["name"] for call in successes}
    group_results: list[dict[str, Any]] = []
    for group in mcp.get("required_tool_groups") or ():
        normalized_group = _unique_tools(group)
        satisfied = bool(names.intersection(normalized_group))
        group_results.append({"tools": normalized_group, "satisfied": satisfied})
        if not satisfied:
            _record_reason(reasons, "required_tool_group_unsatisfied")
    if bool(mcp.get("render_required")) and not any(_is_render_tool(name) for name in names):
        _record_reason(reasons, "required_render_not_successful")

    return {
        **health,
        "status": "eligible" if not reasons else "agent_noncompliant",
        "reason_codes": reasons,
        "successful_mcp_calls": len(successes),
        "successful_tools": [call["name"] for call in successes],
        "evidence_required": True,
        "required_tool_groups": group_results,
    }
