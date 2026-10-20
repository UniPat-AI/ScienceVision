"""Structural security contract for containerized ScienceVision evaluations.

This module deliberately validates *artifacts*, not model behaviour.  A task
prompt can ask an agent not to inspect a scorer, but only a task tree with a
separate verifier surface, a constrained network policy, and a pinned runtime
can make that request enforceable.

The helpers are dependency-free so both task generation and launch preflight
can use them.  They do not start Docker or a Harbor trial: callers must still
perform a same-runtime egress canary before an official run.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # Python 3.10 remains supported by this repository.
    import tomli as tomllib  # type: ignore[no-redef]


SECURITY_CONTRACT_VERSION = "sciencevision.agent_boundary.v1"
SECURITY_ATTESTATION_STATUS = "runtime-attested"
SECURITY_ATTESTATION_SCOPE = "trusted-host-runtime"
_MCP_HTTP_URL = "http://127.0.0.1:8765/mcp"
_MCP_HTTP_HEALTH_PATH = "/run/sciencevision-mcp/health.json"
_TASK_ASSET_MANIFEST_NAME = "mcp-task-assets.json"
_TASK_ASSET_MANIFEST_SCHEMA = "sciencevision.mcp_task_assets.v1"
_ASSET_VISIBILITY_POLICY_VERSION = "sciencevision.route_asset_visibility.v1"
_PRIVATE_ASSET_ROLES = frozenset(
    {
        "mask",
        "label",
        "labels",
        "segmentation",
        "segmentation_sidecar",
        "annotation",
        "annotation_sidecar",
        "ground_truth",
        "ground_truth_sidecar",
        "answer_key",
        "target_mask",
    }
)

# A task's public metadata may be visible to the agent runtime.  These values
# are useful only to a scorer or can reveal a source-side task layout, so they
# must live exclusively in the verifier-private metadata blob.
PRIVATE_METADATA_KEYS = frozenset(
    {
        "ground_truth",
        "scorer",
        "scorer_config",
        "weights",
        "provenance",
        "recompute_receipt",
        "grounding_receipt",
        "grounding_receipts",
        "asset_hashes",
        "asset_grounding",
        "input_closure",
        "challenge_controls",
        "raw_task",
        "repo_path",
        "source_dataset",
        "source_qid",
    }
)

# These locations are evaluator/runtime internals, never task inputs.  The
# list is intentionally narrow: a scanner may flag direct evidence, but must
# not turn a generic shell command into a false positive.
PROTECTED_PATH_PREFIXES = (
    "/app/sci3d_bench",
    "/app/harbor_adapters",
    "/opt/sci3d",
    "/tests",
    "/logs/verifier",
)

_HOST_RE = re.compile(
    r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\."
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*$"
)
_DIGEST_RE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_HOST_PATH_RE = re.compile(r"(?:^|[\s\"'])/(?:Users|home|workspace|workspaces)/", re.IGNORECASE)


class SecurityContractError(ValueError):
    """A generated task tree cannot be used as a hardened evaluation."""


def _normalized_asset_role(value: Any) -> str:
    return str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")


def _is_private_asset_role(value: Any) -> bool:
    role = _normalized_asset_role(value)
    return role in _PRIVATE_ASSET_ROLES or role.startswith("segmentation_")


def _task_leaf(value: Any) -> str:
    """Return one unambiguous task identifier from a result/receipt value."""
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    leaf = Path(text).name
    if leaf in {"", ".", ".."}:
        return ""
    return leaf


def normalize_allowed_hosts(hosts: Iterable[str] | None) -> tuple[str, ...]:
    """Return a strict, order-preserving hostname allowlist.

    Wildcards, URL strings, ports, IP/CIDR ranges, and proxy-like catch-alls
    are deliberately rejected.  The model provider should sit behind one
    purpose-specific gateway when it needs multiple upstream origins.
    """
    out: list[str] = []
    for raw in hosts or ():
        host = str(raw or "").strip().lower().rstrip(".")
        if not host:
            continue
        if (
            "://" in host
            or "/" in host
            or ":" in host
            or "*" in host
            or host in {"localhost", "0.0.0.0", "::1"}
            or not _HOST_RE.fullmatch(host)
        ):
            raise SecurityContractError(
                f"agent allowed host must be one exact DNS hostname, got {raw!r}"
            )
        if host not in out:
            out.append(host)
    return tuple(out)


def network_contract(agent_allowed_hosts: Iterable[str] | None = None) -> dict[str, Any]:
    """Return the public, non-secret network part of the contract.

    No configured model gateway is fail-closed as ``no-network``.  A caller
    launching a live model must require the allowlist variant explicitly.
    """
    hosts = normalize_allowed_hosts(agent_allowed_hosts)
    return {
        "version": SECURITY_CONTRACT_VERSION,
        "agent_network_mode": "allowlist" if hosts else "no-network",
        "agent_allowed_hosts": list(hosts),
        "environment_network_mode": "no-network",
        "verifier_network_mode": "no-network",
        "metadata_visibility": "public_task_toml_private_verifier_tests",
    }


def _walk_metadata(value: Any, *, path: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], Any]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = (*path, str(key))
            yield child_path, child
            yield from _walk_metadata(child, path=child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_metadata(child, path=(*path, str(index)))


def assert_public_metadata_safe(metadata: Mapping[str, Any] | None) -> None:
    """Raise when public task metadata contains verifier-only information."""
    if not isinstance(metadata, Mapping):
        raise SecurityContractError("public metadata must be a mapping")
    for path, value in _walk_metadata(metadata):
        key = path[-1]
        if key in PRIVATE_METADATA_KEYS:
            raise SecurityContractError(
                "private metadata key leaked to task.toml: " + ".".join(path)
            )
        if isinstance(value, str):
            if any(value.startswith(prefix) for prefix in PROTECTED_PATH_PREFIXES):
                raise SecurityContractError(
                    "protected evaluator path leaked to task.toml: " + ".".join(path)
                )
            if _HOST_PATH_RE.search(value):
                raise SecurityContractError(
                    "host filesystem path leaked to task.toml: " + ".".join(path)
                )

    contract = metadata.get("security_contract")
    if not isinstance(contract, Mapping):
        raise SecurityContractError("public metadata is missing security_contract")
    if contract.get("version") != SECURITY_CONTRACT_VERSION:
        raise SecurityContractError("unknown or missing security contract version")


def _route_asset_visibility(metadata: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = metadata.get("route_asset_visibility")
    return value if isinstance(value, Mapping) else None


def _instruction_sci3d_payloads(path: Path) -> list[Mapping[str, Any]]:
    """Parse only generator-owned fenced payloads, not natural-language prose."""
    if not path.is_file():
        raise SecurityContractError(f"missing instruction.md: {path}")
    text = path.read_text(encoding="utf-8")
    blocks = re.findall(r"```sci3d\s*\n(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    payloads: list[Mapping[str, Any]] = []
    for raw in blocks:
        try:
            decoded = json.loads(raw.strip())
        except json.JSONDecodeError as exc:
            raise SecurityContractError("instruction has malformed sci3d payload") from exc
        if not isinstance(decoded, Mapping):
            raise SecurityContractError("instruction sci3d payload must be an object")
        payloads.append(decoded)
    return payloads


def _validate_asset_visibility(
    metadata: Mapping[str, Any], *, instruction_path: Path,
) -> Mapping[str, Any] | None:
    """Verify that public metadata and instruction respect declared visibility.

    The policy itself carries semantic roles only.  It deliberately has no
    source filename, source asset ID, or digest, which makes it safe to put in
    task.toml and lets this static gate catch a later staging regression.
    """
    policy = _route_asset_visibility(metadata)
    if policy is None:
        return None
    if policy.get("schema_version") != _ASSET_VISIBILITY_POLICY_VERSION:
        raise SecurityContractError("unknown route_asset_visibility schema")
    route = str(policy.get("route") or "")
    if route not in {"cc", "ccmcp", "cc2d", "text", "host"}:
        raise SecurityContractError("route_asset_visibility has an unknown route")
    if route in {"cc", "ccmcp"}:
        contract = metadata.get("route_contract")
        if not isinstance(contract, Mapping) or contract.get("route") != route:
            raise SecurityContractError(
                "CC route_asset_visibility must agree with the public route_contract"
            )
    status = str(policy.get("status") or "")
    if status not in {"eligible", "route_ineligible"}:
        raise SecurityContractError("route_asset_visibility has an invalid status")
    reason = policy.get("reason_code")
    if status == "route_ineligible":
        if metadata.get("evaluation_eligible") is not False:
            raise SecurityContractError("route-ineligible task must be evaluation_eligible=false")
        if not isinstance(reason, str) or not reason:
            raise SecurityContractError("route-ineligible task has no public reason_code")
    elif reason not in {"", None}:
        raise SecurityContractError("eligible task may not carry an ineligibility reason")

    private_raw = policy.get("private_asset_roles")
    public_raw = policy.get("explicit_public_input_roles")
    if (
        isinstance(private_raw, (str, bytes))
        or not isinstance(private_raw, list)
        or isinstance(public_raw, (str, bytes))
        or not isinstance(public_raw, list)
    ):
        raise SecurityContractError("route_asset_visibility role lists are malformed")
    private_roles = {_normalized_asset_role(value) for value in private_raw}
    public_roles = {_normalized_asset_role(value) for value in public_raw}
    if not all(role and _is_private_asset_role(role) for role in private_roles):
        raise SecurityContractError("route_asset_visibility lists a non-private role")

    sensitive_fields = {
        "mask_path": "mask",
        "mask_id": "mask",
        "label_path": "label",
        "segmentation_path": "segmentation",
        "annotation_path": "annotation",
    }
    for field, role in sensitive_fields.items():
        if field in metadata and role not in public_roles:
            raise SecurityContractError(f"private {role} leaked to public metadata via {field}")
    assets = metadata.get("assets") or []
    if isinstance(assets, (str, bytes)) or not isinstance(assets, list):
        raise SecurityContractError("public assets must be a list")
    for row in assets:
        if not isinstance(row, Mapping):
            raise SecurityContractError("public asset row must be a mapping")
        role = _normalized_asset_role(row.get("role"))
        if _is_private_asset_role(role) and role not in public_roles:
            raise SecurityContractError(f"private asset role leaked to public metadata: {role}")

    for payload in _instruction_sci3d_payloads(instruction_path):
        for field, role in sensitive_fields.items():
            if field in payload and role not in public_roles:
                raise SecurityContractError(f"private {role} leaked to instruction via {field}")
        rows = payload.get("assets") or []
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, Mapping):
                    role = _normalized_asset_role(row.get("role"))
                    if _is_private_asset_role(role) and role not in public_roles:
                        raise SecurityContractError(
                            f"private asset role leaked to instruction: {role}"
                        )
    return policy


def _task_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text())
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise SecurityContractError(f"cannot parse {path}: {type(exc).__name__}") from exc


def _validate_network(cfg: Mapping[str, Any], *, section: str, require_allowlist: bool) -> None:
    mode = cfg.get("network_mode")
    hosts = cfg.get("allowed_hosts")
    if section in {"environment", "verifier"}:
        if mode != "no-network":
            raise SecurityContractError(f"[{section}] must use network_mode=no-network")
        if hosts:
            raise SecurityContractError(f"[{section}] may not declare allowed_hosts")
        return

    if mode == "no-network":
        if hosts:
            raise SecurityContractError("[agent] no-network may not declare allowed_hosts")
        if require_allowlist:
            raise SecurityContractError("live agent requires a non-empty exact-host allowlist")
        return
    if mode != "allowlist":
        raise SecurityContractError("[agent] must use no-network or allowlist, never public")
    normalized = normalize_allowed_hosts(hosts if isinstance(hosts, list) else ())
    if not normalized:
        raise SecurityContractError("[agent] allowlist must contain at least one exact host")


def _validate_dockerfile(path: Path, *, require_image_digest: bool) -> None:
    if not path.is_file():
        raise SecurityContractError(f"missing agent Dockerfile: {path}")
    refs = [
        line.split(maxsplit=1)[1].strip()
        for line in path.read_text().splitlines()
        if line.startswith("FROM ") and len(line.split(maxsplit=1)) == 2
    ]
    if len(refs) != 1:
        raise SecurityContractError(f"expected exactly one agent FROM image in {path}")
    if require_image_digest and not _DIGEST_RE.fullmatch(refs[0]):
        raise SecurityContractError(f"agent image is not digest-pinned: {refs[0]!r}")
    text = path.read_text()
    if "sci3d_bench" in text or "harbor_adapters" in text:
        raise SecurityContractError(f"agent Dockerfile references evaluator package: {path}")


def _validate_mcp_task_asset_manifest(
    path: Path,
    *,
    asset_dir: Path,
    asset_visibility: Mapping[str, Any] | None,
) -> None:
    """Make the worker's private asset registry a complete staged-file map."""
    if not path.is_file():
        raise SecurityContractError(f"missing MCP task asset manifest: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SecurityContractError(f"cannot parse MCP task asset manifest: {path}") from exc
    if not isinstance(payload, Mapping) or payload.get("schema_version") != _TASK_ASSET_MANIFEST_SCHEMA:
        raise SecurityContractError("MCP task asset manifest has an unknown schema")
    rows = payload.get("assets")
    if isinstance(rows, (str, bytes)) or not isinstance(rows, list):
        raise SecurityContractError("MCP task asset manifest assets must be a list")
    public_roles: set[str] = set()
    if isinstance(asset_visibility, Mapping):
        raw = asset_visibility.get("explicit_public_input_roles")
        if isinstance(raw, list):
            public_roles = {_normalized_asset_role(value) for value in raw}

    seen_ids: set[str] = set()
    staged_relpaths: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise SecurityContractError("MCP task asset manifest row must be a mapping")
        if any(key in row for key in ("sha256", "repo_path", "mask_path", "source_path")):
            raise SecurityContractError("MCP task asset manifest may not contain source provenance")
        asset_id = str(row.get("asset_id") or "").strip()
        if not asset_id or asset_id in seen_ids:
            raise SecurityContractError("MCP task asset manifest asset_id values must be unique")
        seen_ids.add(asset_id)
        container_path = str(row.get("asset_path") or "").strip()
        parsed = PurePosixPath(container_path)
        if (
            not container_path.startswith("/data/")
            or ".." in parsed.parts
            or len(parsed.parts) < 3
        ):
            raise SecurityContractError("MCP task asset manifest has an unsafe asset_path")
        relative = PurePosixPath(*parsed.parts[2:]).as_posix()
        if relative in staged_relpaths:
            raise SecurityContractError("MCP task asset manifest asset paths must be unique")
        staged_relpaths.add(relative)
        staged_file = asset_dir.joinpath(*PurePosixPath(relative).parts)
        if not staged_file.is_file():
            raise SecurityContractError(
                "MCP task asset manifest refers to an unstaged file: " + container_path
            )
        role = _normalized_asset_role(row.get("role"))
        if _is_private_asset_role(role) and role not in public_roles:
            raise SecurityContractError(
                "MCP worker manifest exposes a verifier-private asset role: " + role
            )

    actual_relpaths = {
        item.relative_to(asset_dir).as_posix()
        for item in asset_dir.rglob("*")
        if item.is_file()
    } if asset_dir.exists() else set()
    if actual_relpaths != staged_relpaths:
        raise SecurityContractError(
            "MCP task asset manifest must enumerate exactly every staged asset file"
        )


def _validate_mcp_worker(
    agent_dockerfile: Path,
    worker_dockerfile: Path,
    *,
    asset_dir: Path,
    manifest_path: Path,
    asset_visibility: Mapping[str, Any] | None,
) -> None:
    """Validate the generated two-container MCP trust boundary.

    These checks intentionally inspect only generated build-context files.  A
    base-image digest and an actual runtime egress canary remain launcher
    responsibilities, which is why this is paired with attestation validation.
    """
    if not worker_dockerfile.is_file():
        raise SecurityContractError(f"missing MCP worker Dockerfile: {worker_dockerfile}")
    agent = agent_dockerfile.read_text()
    worker = worker_dockerfile.read_text()
    if re.search(r"^\s*COPY\s+asset/", agent, re.MULTILINE):
        raise SecurityContractError("CC-MCP agent Dockerfile may not COPY raw asset/")
    if "sci3d_agent" in agent.lower() or re.search(
        r"^\s*(?:RUN|CMD|ENTRYPOINT)\b.*\bsciencevision-mcp\b",
        agent,
        flags=re.IGNORECASE | re.MULTILINE,
    ):
        raise SecurityContractError("CC-MCP agent Dockerfile may not contain ScienceVision server code")
    if not re.search(r"^\s*COPY\s+asset/\s+/data/", worker, re.MULTILINE):
        raise SecurityContractError("MCP worker Dockerfile must be the sole COPY asset/ -> /data/ holder")
    if _TASK_ASSET_MANIFEST_NAME not in worker:
        raise SecurityContractError("MCP worker Dockerfile must COPY the task asset manifest")
    _validate_mcp_task_asset_manifest(
        manifest_path,
        asset_dir=asset_dir,
        asset_visibility=asset_visibility,
    )
    # The pinned worker image owns its entrypoint/health implementation. The
    # task-local Dockerfile must only bind that trusted image to the staged
    # input; requiring CMD/ENV here would duplicate C's worker packaging.


def _validate_compose(path: Path, *, require_hardening_compose: bool, is_mcp_route: bool) -> None:
    if not path.exists():
        if require_hardening_compose:
            raise SecurityContractError(f"missing hardened compose overlay: {path}")
        return
    text = path.read_text()
    forbidden = (
        "network_mode:",
        "networks:",
        "privileged: true",
        "/var/run/docker.sock",
        "ports:",
    )
    hit = [token for token in forbidden if token in text]
    if hit:
        raise SecurityContractError(
            f"compose must not bypass egress or elevate privileges ({', '.join(hit)})"
        )
    if not is_mcp_route:
        raise SecurityContractError("compose overlay is reserved for an explicit CC-MCP route")
    for expected_text in (
        "services:",
        "  main:",
        "    depends_on:",
        "      sciencevision-mcp:",
        "        condition: service_healthy",
        "  sciencevision-mcp:",
        "dockerfile: Dockerfile.mcp-worker",
        'SV_HTTP_PORT: "8765"',
        'SV_HTTP_HOST: "0.0.0.0"',
        'SV_MCP_CATALOG_SHA256: "',
        'SV_MCP_SCHEMA_SHA256: "',
        f'SV_MCP_HEALTH_RECEIPT_PATH: "{_MCP_HTTP_HEALTH_PATH}"',
        f'SV_MCP_HTTP_SURFACE: "{_MCP_HTTP_URL}"',
        "mcp_worker_healthcheck.py",
    ):
        if expected_text not in text:
            raise SecurityContractError(f"CC-MCP compose overlay is missing {expected_text!r}")


def _route_contract(metadata: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = metadata.get("route_contract")
    return value if isinstance(value, Mapping) else None


def _is_ccmcp_route(metadata: Mapping[str, Any]) -> bool:
    contract = _route_contract(metadata)
    return bool(contract and contract.get("route") == "ccmcp")


def _validate_route_contract(metadata: Mapping[str, Any]) -> None:
    """Require a complete catalog whenever a route claims CCMCP semantics."""
    contract = _route_contract(metadata)
    if contract is None:
        return
    route = contract.get("route")
    mcp = contract.get("mcp")
    if route not in {"cc", "ccmcp"} or not isinstance(mcp, Mapping):
        raise SecurityContractError("malformed public route_contract")
    if route == "cc":
        if mcp.get("exposed") or mcp.get("required") or mcp.get("allowed_tools"):
            raise SecurityContractError("CC route_contract must carry a negative MCP guarantee")
        return
    if mcp.get("exposed") is not True:
        raise SecurityContractError("CC-MCP route_contract must declare MCP exposed")
    catalog = mcp.get("catalog")
    if not isinstance(catalog, Mapping):
        raise SecurityContractError("CC-MCP route_contract is missing a worker surface catalog")
    names = catalog.get("tool_names")
    if not isinstance(names, list) or not names:
        raise SecurityContractError("CC-MCP route_contract has an empty worker tool catalog")
    for key in ("catalog_sha256", "schema_sha256"):
        value = catalog.get(key)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise SecurityContractError(f"CC-MCP route_contract has invalid {key}")


def validate_task_tree(
    task_dir: Path,
    *,
    require_agent_allowlist: bool = False,
    require_image_digest: bool = True,
    require_hardening_compose: bool = False,
) -> dict[str, Any]:
    """Validate one generated task tree before a hardened Harbor launch.

    This is a static preflight.  It intentionally cannot attest to a running
    container's egress enforcement; a launcher must pair it with a same-binary
    Harbor egress canary and record that result outside agent-writable storage.
    """
    root = Path(task_dir)
    config = _task_toml(root / "task.toml")
    metadata = config.get("metadata")
    assert_public_metadata_safe(metadata if isinstance(metadata, Mapping) else None)
    assert isinstance(metadata, Mapping)  # narrowed by assert_public_metadata_safe
    asset_visibility = _validate_asset_visibility(
        metadata, instruction_path=root / "instruction.md",
    )
    _validate_route_contract(metadata)

    environment = config.get("environment")
    agent = config.get("agent")
    verifier = config.get("verifier")
    if not all(isinstance(x, Mapping) for x in (environment, agent, verifier)):
        raise SecurityContractError("task.toml is missing agent/verifier/environment sections")
    _validate_network(environment, section="environment", require_allowlist=False)
    _validate_network(agent, section="agent", require_allowlist=require_agent_allowlist)
    _validate_network(verifier, section="verifier", require_allowlist=False)
    contract = metadata.get("security_contract")
    assert isinstance(contract, Mapping)
    if contract.get("agent_network_mode") != agent.get("network_mode"):
        raise SecurityContractError("public security_contract disagrees with [agent] network_mode")
    if list(contract.get("agent_allowed_hosts") or []) != list(agent.get("allowed_hosts") or []):
        raise SecurityContractError("public security_contract disagrees with [agent] allowed_hosts")

    if verifier.get("environment_mode") != "separate":
        raise SecurityContractError("hardened task requires verifier.environment_mode=separate")
    verifier_env = verifier.get("environment")
    if not isinstance(verifier_env, Mapping):
        raise SecurityContractError("hardened task requires a separate verifier environment")
    _validate_network(verifier_env, section="verifier", require_allowlist=False)

    agent_dockerfile = root / "environment" / "Dockerfile"
    _validate_dockerfile(agent_dockerfile, require_image_digest=require_image_digest)
    is_mcp_route = _is_ccmcp_route(metadata)
    route = _route_contract(metadata)
    if is_mcp_route:
        _validate_mcp_worker(
            agent_dockerfile,
            root / "environment" / "Dockerfile.mcp-worker",
            asset_dir=root / "environment" / "asset",
            manifest_path=root / "environment" / _TASK_ASSET_MANIFEST_NAME,
            asset_visibility=asset_visibility,
        )
    _validate_compose(
        root / "environment" / "docker-compose.yaml",
        require_hardening_compose=require_hardening_compose or is_mcp_route,
        is_mcp_route=is_mcp_route,
    )
    return {
        "schema_version": SECURITY_CONTRACT_VERSION,
        "task_dir": str(root),
        "agent_network_mode": agent.get("network_mode"),
        "agent_allowed_hosts": list(agent.get("allowed_hosts") or []),
        "status": "pass",
    }


def validate_security_attestation(
    attestation: Mapping[str, Any] | None,
    *,
    expected_task_names: Iterable[str] = (),
) -> dict[str, Any]:
    """Validate the host-side receipt required for an official aggregate.

    Static task-tree validation proves only what was *generated*.  An official
    number also needs evidence that the same Harbor runtime enforced the
    declared egress policy while it ran.  This function validates the compact,
    JSON-only receipt shape that a trusted launcher must write outside every
    agent-writable volume.  It intentionally does not pretend to verify a
    cryptographic signature: deployments that require one should verify it
    before passing the decoded receipt here.

    Expected receipt shape::

        {
          "schema_version": "sciencevision.agent_boundary.v1",
          "status": "runtime-attested",
          "attestation_scope": "trusted-host-runtime",
          "issuer": "harbor-launcher",
          "egress_canary": {
            "status": "pass", "same_runtime": true,
            "agent_egress_blocked": true,
            "environment_egress_blocked": true,
            "verifier_egress_blocked": true,
            "agent_policy_enforced": true
          },
          "tasks": [{"task_name": "sv-...", "task_toml_sha256": "..."}]
        }

    ``expected_task_names`` normally comes from the result set.  Every
    reported trial must be covered by this receipt; otherwise a recycled
    receipt from a different job could make an unrelated score look hardened.
    """
    if not isinstance(attestation, Mapping):
        raise SecurityContractError("official aggregation requires a security attestation object")
    if attestation.get("schema_version") != SECURITY_CONTRACT_VERSION:
        raise SecurityContractError("security attestation has an unknown schema_version")
    if attestation.get("status") != SECURITY_ATTESTATION_STATUS:
        raise SecurityContractError(
            "security attestation is not a runtime attestation (preflight is insufficient)"
        )
    if attestation.get("attestation_scope") != SECURITY_ATTESTATION_SCOPE:
        raise SecurityContractError("security attestation has no trusted-host runtime scope")
    issuer = attestation.get("issuer")
    if not isinstance(issuer, str) or not issuer.strip():
        raise SecurityContractError("security attestation is missing a trusted issuer")

    canary = attestation.get("egress_canary")
    if not isinstance(canary, Mapping):
        raise SecurityContractError("security attestation is missing its egress canary")
    if canary.get("status") != "pass":
        raise SecurityContractError("security attestation egress canary did not pass")
    for key in (
        "same_runtime",
        "agent_egress_blocked",
        "environment_egress_blocked",
        "verifier_egress_blocked",
        "agent_policy_enforced",
    ):
        if canary.get(key) is not True:
            raise SecurityContractError(
                f"security attestation egress canary lacks {key}=true"
            )

    raw_tasks = attestation.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise SecurityContractError("security attestation must cover at least one task")
    attested_names: set[str] = set()
    task_hashes: dict[str, str] = {}
    for index, row in enumerate(raw_tasks):
        if not isinstance(row, Mapping):
            raise SecurityContractError(f"security attestation task {index} is not an object")
        name = _task_leaf(row.get("task_name") or row.get("task_id") or row.get("task_dir"))
        if not name:
            raise SecurityContractError(f"security attestation task {index} has no task name")
        digest = row.get("task_toml_sha256")
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise SecurityContractError(
                f"security attestation task {name!r} has no valid task_toml_sha256"
            )
        if name in attested_names:
            raise SecurityContractError(f"security attestation names task {name!r} more than once")
        attested_names.add(name)
        task_hashes[name] = digest

    expected = {_task_leaf(name) for name in expected_task_names}
    if "" in expected:
        raise SecurityContractError("result set has an unscoped trial name")
    missing = sorted(expected - attested_names)
    if missing:
        raise SecurityContractError(
            "security attestation does not cover reported task(s): " + ", ".join(missing)
        )
    return {
        "schema_version": SECURITY_CONTRACT_VERSION,
        "status": SECURITY_ATTESTATION_STATUS,
        "attestation_scope": SECURITY_ATTESTATION_SCOPE,
        "issuer": issuer.strip(),
        "n_attested_tasks": len(attested_names),
        "attested_task_names": sorted(attested_names),
        "task_toml_sha256": task_hashes,
    }


def security_manifest_payload(task_dirs: Iterable[Path]) -> dict[str, Any]:
    """Return a deterministic, host-side manifest skeleton for launchers.

    The caller owns persistence and any signature.  Keeping this pure prevents
    a task's agent container from manufacturing its own positive attestation.
    """
    rows = []
    for task_dir in sorted((Path(x) for x in task_dirs), key=lambda p: str(p)):
        report = validate_task_tree(task_dir)
        blob = (task_dir / "task.toml").read_bytes()
        rows.append({
            "task_dir": str(task_dir),
            "task_toml_sha256": hashlib.sha256(blob).hexdigest(),
            "agent_network_mode": report["agent_network_mode"],
            "agent_allowed_hosts": report["agent_allowed_hosts"],
        })
    return {
        "schema_version": SECURITY_CONTRACT_VERSION,
        "status": "preflight-pass",
        "tasks": rows,
    }


def dump_security_manifest(task_dirs: Iterable[Path]) -> str:
    """Stable JSON useful to launchers that write a host-side receipt."""
    return json.dumps(security_manifest_payload(task_dirs), sort_keys=True, indent=2) + "\n"
