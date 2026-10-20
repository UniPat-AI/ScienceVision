"""Canonical serialization and common artifact/decision envelopes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Mapping

from .errors import InputError


SCHEMA_VERSION = 1
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_json(value: Any) -> bytes:
    """Return deterministic UTF-8 JSON suitable for content-addressed digests."""
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise InputError(f"value cannot be canonically serialized: {exc}") from exc
    return text.encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Return the unprefixed SHA-256 of :func:`canonical_json`.

    Registry contracts use ``sha256:<hex>`` references, while local fixture
    specifications use the raw hexadecimal digest. Keeping one serializer and
    exposing both representations prevents those two layers from drifting.
    """
    return sha256(canonical_json(value)).hexdigest()


def digest(value: Any) -> str:
    return f"sha256:{canonical_sha256(value)}"


def digest_bytes(value: bytes) -> str:
    return f"sha256:{sha256(value).hexdigest()}"


def require_digest(value: str, field: str = "digest") -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise InputError(f"{field} must be a sha256: digest")
    return value


def require_nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{field} must be a non-empty string")
    return value.strip()


def make_envelope(
    *,
    kind: str,
    identifier: str,
    status: str,
    body: Mapping[str, Any],
    producer: str = "qagen/0.1.0",
    created_at: str | None = None,
) -> dict[str, Any]:
    """Create a common artifact envelope.

    The digest covers semantic body content only. Receipt bodies include their
    decision timestamp, while content snapshots can remain stable across writes.
    """
    require_nonempty_string(kind, "kind")
    require_nonempty_string(identifier, "identifier")
    require_nonempty_string(status, "status")
    if not isinstance(body, Mapping):
        raise InputError("envelope body must be a mapping")
    body_dict = dict(body)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "id": identifier,
        "status": status,
        "content_digest": digest(body_dict),
        "created_at": created_at or utc_now(),
        "producer": producer,
        "body": body_dict,
    }


def verify_envelope(value: Mapping[str, Any], *, kind: str | None = None) -> dict[str, Any]:
    required = {"schema_version", "kind", "id", "status", "content_digest", "created_at", "producer", "body"}
    missing = sorted(required - set(value))
    if missing:
        raise InputError(f"artifact envelope missing fields: {', '.join(missing)}")
    if value["schema_version"] != SCHEMA_VERSION:
        raise InputError(f"unsupported schema_version: {value['schema_version']}")
    if kind is not None and value["kind"] != kind:
        raise InputError(f"expected artifact kind {kind}, got {value['kind']}")
    if not isinstance(value["body"], Mapping):
        raise InputError("artifact envelope body must be a mapping")
    expected = digest(dict(value["body"]))
    if value["content_digest"] != expected:
        raise InputError("artifact envelope content digest does not match body")
    require_digest(value["content_digest"], "content_digest")
    return dict(value)


def make_decision(
    *,
    kind: str,
    identifier: str,
    decision: str,
    actor_id: str,
    actor_role: str,
    bound_digests: Mapping[str, Any],
    rationale: str = "",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "decision": require_nonempty_string(decision, "decision"),
        "actor_id": require_nonempty_string(actor_id, "actor_id"),
        "actor_role": require_nonempty_string(actor_role, "actor_role"),
        "decided_at": utc_now(),
        "bound_digests": dict(bound_digests),
        "rationale": rationale,
    }
    if extra:
        body.update(dict(extra))
    return make_envelope(kind=kind, identifier=identifier, status=decision, body=body)


@dataclass(frozen=True)
class ArtifactRef:
    kind: str
    identifier: str
    content_digest: str

    def __post_init__(self) -> None:
        require_nonempty_string(self.kind, "artifact ref kind")
        require_nonempty_string(self.identifier, "artifact ref identifier")
        require_digest(self.content_digest, "artifact ref content_digest")

    def as_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "id": self.identifier,
            "content_digest": self.content_digest,
        }
