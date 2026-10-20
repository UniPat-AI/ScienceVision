"""Fail-closed loader for the local model-routing control-plane document."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft7Validator
import yaml

from .contracts import digest
from .errors import GatePendingError, InputError
from .storage.atomic import read_json


class RoutingPolicyError(InputError):
    """The checked-in routing document is malformed or violates role policy."""


_ROUTING_SUGGESTIONS = {
    "brief_builder": ("anthropic", "opus-5"),
    "planner": ("anthropic", "opus-5"),
    "author": ("deepseek", "dpsk-v4-pro"),
    "truth_builder": ("deepseek", "dpsk-v4-pro"),
    "evidence_worker": ("deepseek", "dpsk-v4-pro"),
    "rewriter": ("deepseek", "dpsk-v4-pro"),
    "verifier": ("openai", "gpt-5.6-sol"),
    "adversarial_check": ("openai", "gpt-5.6-sol"),
}
_PILOT_ROUTES = {
    "brief_builder": ("anthropic", "claude-opus-5"),
    "planner": ("anthropic", "claude-opus-5"),
    "author": ("deepseek", "deepseek-v4-pro"),
    "truth_builder": ("deepseek", "deepseek-v4-pro"),
    "evidence_worker": ("deepseek", "deepseek-v4-pro"),
    "rewriter": ("deepseek", "deepseek-v4-pro"),
    "verifier": ("openai", "gpt-5.6-sol"),
    "adversarial_check": ("openai", "gpt-5.6-sol"),
}
_ROLE_ALIASES = {"planner": "brief_builder", "adversarial_check": "verifier"}


def _read_yaml_or_json(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) if path.suffix.lower() in {".yaml", ".yml"} else read_json(path)
    except (OSError, yaml.YAMLError, InputError) as exc:
        raise RoutingPolicyError(f"cannot read model routing document {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise RoutingPolicyError("model routing document must be an object")
    return dict(value)


def _validate(document: Mapping[str, Any], schema_path: Path) -> None:
    if not schema_path.is_file():
        raise RoutingPolicyError(f"model routing schema is unavailable: {schema_path}")
    schema = read_json(schema_path)
    if not isinstance(schema, Mapping):
        raise RoutingPolicyError("model routing schema is not an object")
    errors = sorted(Draft7Validator(schema).iter_errors(document), key=lambda error: list(error.absolute_path))
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise RoutingPolicyError(f"model routing schema validation failed at {location}: {error.message}")


def _worker_policy(document: Mapping[str, Any]) -> dict[str, Any]:
    providers = document.get("providers")
    roles = document.get("roles")
    if not isinstance(providers, Mapping) or not isinstance(roles, Mapping):
        raise RoutingPolicyError("model routing requires providers and roles mappings")
    normalized_roles: dict[str, dict[str, Any]] = {}
    expected_routes = _PILOT_ROUTES if document.get("routing_status") == "approved_local_pilot" else _ROUTING_SUGGESTIONS
    for role, expected_route in expected_routes.items():
        configured_role = _ROLE_ALIASES.get(role, role)
        raw = roles.get(configured_role)
        if not isinstance(raw, Mapping):
            raise RoutingPolicyError(f"model routing lacks required role: {configured_role}")
        provider, model = expected_route
        models = raw.get("allowed_models")
        if raw.get("provider") != provider or not isinstance(models, list) or models != [model]:
            raise RoutingPolicyError(f"model routing role {configured_role} does not match the approved logical route")
        normalized_roles[role] = dict(raw)
    author = normalized_roles["author"]
    verifier = normalized_roles["verifier"]
    if (author.get("provider"), tuple(author.get("allowed_models", []))) == (
        verifier.get("provider"),
        tuple(verifier.get("allowed_models", [])),
    ):
        raise RoutingPolicyError("author and verifier cannot share the same provider/model route")
    return {"providers": dict(providers), "roles": normalized_roles}


def load_model_routing(routing_path: str | Path, *, schema_path: str | Path) -> dict[str, Any]:
    """Load a schema-validated routing map without enabling any provider call."""
    path = Path(routing_path).expanduser().resolve()
    schema = Path(schema_path).expanduser().resolve()
    if not path.is_file():
        raise RoutingPolicyError(f"model routing document is not a local file: {path}")
    document = _read_yaml_or_json(path)
    _validate(document, schema)
    policy = _worker_policy(document)
    return {
        "routing_path": str(path),
        "routing_digest": digest(document),
        "document": document,
        "worker_policy": policy,
        "execution_eligible": bool(
            document.get("enabled")
            and document.get("run_eligibility") in {"eligible", "approved_local_pilot"}
            and all(bool(role.get("enabled")) for role in policy["roles"].values())
        ),
    }


def require_execution_eligible(routing: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the worker policy only after an explicitly enabled routing gate."""
    if routing.get("execution_eligible") is not True:
        raise GatePendingError("model routing is logical/default-only; execution is not authorized")
    policy = routing.get("worker_policy")
    if not isinstance(policy, Mapping):
        raise RoutingPolicyError("routing has no worker policy")
    return policy
