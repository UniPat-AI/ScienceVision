"""Append-only event and usage ledgers with atomic budget reservations.

The usage ledger is intentionally a journal rather than a mutable balance.  A
reservation is appended before work starts and exactly one settlement records
the actual result.  Every read/check/append sequence shares an advisory OS
lock, so independent local processes cannot both spend the same budget or
settle the same reservation.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import math
import os
from pathlib import Path
import secrets
import threading
from typing import Any, Mapping
from types import MappingProxyType

from ..contracts import canonical_json, digest, make_envelope, require_digest, verify_envelope
from ..paths import WorkspacePaths


_LEDGER_THREAD_LOCK = threading.RLock()
_LEGACY_RUN_ID = "control-plane"


class UsageLedgerError(ValueError):
    """A usage ledger record is malformed, missing, or internally inconsistent."""


class UsageBudgetExceededError(UsageLedgerError):
    """A reservation would exceed the immutable run or role budget."""


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise UsageLedgerError(f"{field} must be a non-empty string")
    return value.strip()


def _nonnegative_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise UsageLedgerError(f"{field} must be a non-negative integer")
    return value


def _nonnegative_number(value: Any, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0 or not math.isfinite(float(value)):
        raise UsageLedgerError(f"{field} must be a finite non-negative number")
    return float(value)


@dataclass(frozen=True)
class UsageBudget:
    """A digestable, immutable cap shared by every reservation in one run.

    ``role_max_*`` must contain a cap for every role that can reserve work in
    the run.  Keeping the role caps in the same immutable policy prevents a
    later caller from relaxing an exhausted role by submitting a new budget.
    """

    total_max_tokens: int
    total_max_cost_usd: float
    role_max_tokens: Mapping[str, int]
    role_max_cost_usd: Mapping[str, float]

    def __post_init__(self) -> None:
        total_tokens = _nonnegative_int(self.total_max_tokens, "total_max_tokens")
        total_cost = _nonnegative_number(self.total_max_cost_usd, "total_max_cost_usd")
        raw_tokens = dict(self.role_max_tokens)
        raw_costs = dict(self.role_max_cost_usd)
        tokens: dict[str, int] = {}
        costs: dict[str, float] = {}
        if set(raw_tokens) != set(raw_costs) or not raw_tokens:
            raise UsageLedgerError("role token and cost budgets must name the same non-empty roles")
        for role in sorted(raw_tokens):
            _require_text(role, "budget role")
            tokens[role] = _nonnegative_int(raw_tokens[role], f"role_max_tokens[{role}]")
            costs[role] = _nonnegative_number(raw_costs[role], f"role_max_cost_usd[{role}]")
        object.__setattr__(self, "total_max_tokens", total_tokens)
        object.__setattr__(self, "total_max_cost_usd", total_cost)
        object.__setattr__(self, "role_max_tokens", MappingProxyType(tokens))
        object.__setattr__(self, "role_max_cost_usd", MappingProxyType(costs))

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_max_tokens": self.total_max_tokens,
            "total_max_cost_usd": self.total_max_cost_usd,
            "roles": {
                role: {
                    "max_tokens": self.role_max_tokens[role],
                    "max_cost_usd": self.role_max_cost_usd[role],
                }
                for role in sorted(self.role_max_tokens)
            },
        }

    @property
    def content_digest(self) -> str:
        return digest(self.as_dict())

    @classmethod
    def from_value(cls, value: "UsageBudget | Mapping[str, Any]") -> "UsageBudget":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise UsageLedgerError("usage budget must be a UsageBudget or mapping")

        # Accept the explicit public shape and a compact ``total``/``roles``
        # shape so callers do not need to hand-build dataclasses from YAML.
        total = value.get("total") if isinstance(value.get("total"), Mapping) else value
        total_tokens = total.get("max_tokens", total.get("total_max_tokens"))
        total_cost = total.get("max_cost_usd", total.get("total_max_cost_usd"))
        roles = value.get("roles", value.get("role_limits"))
        if not isinstance(roles, Mapping):
            raise UsageLedgerError("usage budget roles must be a mapping")
        role_tokens: dict[str, int] = {}
        role_costs: dict[str, float] = {}
        for raw_role, raw_limit in roles.items():
            role = _require_text(raw_role, "budget role")
            if not isinstance(raw_limit, Mapping):
                raise UsageLedgerError(f"usage budget for role {role} must be a mapping")
            role_tokens[role] = _nonnegative_int(raw_limit.get("max_tokens"), f"roles.{role}.max_tokens")
            role_costs[role] = _nonnegative_number(raw_limit.get("max_cost_usd"), f"roles.{role}.max_cost_usd")
        return cls(
            total_max_tokens=_nonnegative_int(total_tokens, "total_max_tokens"),
            total_max_cost_usd=_nonnegative_number(total_cost, "total_max_cost_usd"),
            role_max_tokens=role_tokens,
            role_max_cost_usd=role_costs,
        )


@contextmanager
def _exclusive_ledger(path: Path):
    """Hold a thread and process exclusive lock for a ledger operation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LEDGER_THREAD_LOCK:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            # ``flock`` is deliberately held across parsing, validation, and
            # append.  O_APPEND alone cannot make a read/check/append sequence
            # atomic across processes.
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
            yield fd
        finally:
            try:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


def _write_all(fd: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(fd, payload[offset:])
        if written <= 0:
            raise OSError("failed to append full ledger record")
        offset += written


def _append_jsonl_fd(fd: int, record: Mapping[str, Any]) -> None:
    import json

    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    os.lseek(fd, 0, os.SEEK_END)
    _write_all(fd, payload.encode("utf-8"))
    os.fsync(fd)


def _append_jsonl(path: Path, record: Mapping[str, Any]) -> None:
    with _exclusive_ledger(path) as fd:
        _append_jsonl_fd(fd, record)


def _usage_records_fd(fd: int) -> list[dict[str, Any]]:
    import json

    records: list[dict[str, Any]] = []
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        for number, line in enumerate(b"".join(chunks).decode("utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, Mapping):
                raise UsageLedgerError(f"usage ledger record {number} is not an object")
            records.append(dict(value))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UsageLedgerError(f"cannot read append-only usage ledger: {exc}") from exc
    return records


def _legacy_scope(*, worker: str, model: str, input_contract_digest: str) -> dict[str, str]:
    """Bind old control-plane callers without pretending they are a run worker."""
    worker_value = _require_text(worker, "worker")
    model_value = _require_text(model, "model")
    require_digest(input_contract_digest, "input_contract_digest")
    provider = model_value.split(":", 1)[0] if ":" in model_value else "local"
    return {
        "run_id": _LEGACY_RUN_ID,
        "work_item_id": f"legacy:{worker_value}:{input_contract_digest.split(':', 1)[1][:16]}",
        "worker": worker_value,
        "provider": provider,
        "model": model_value,
        "input_contract_digest": input_contract_digest,
    }


def _scope(
    *,
    worker: str,
    model: str,
    input_contract_digest: str,
    run_id: str | None,
    work_item_id: str | None,
    provider: str | None,
) -> dict[str, str]:
    if run_id is None and work_item_id is None and provider is None:
        return _legacy_scope(worker=worker, model=model, input_contract_digest=input_contract_digest)
    worker_value = _require_text(worker, "worker")
    model_value = _require_text(model, "model")
    require_digest(input_contract_digest, "input_contract_digest")
    return {
        "run_id": _require_text(run_id, "run_id"),
        "work_item_id": _require_text(work_item_id, "work_item_id"),
        "worker": worker_value,
        "provider": _require_text(provider, "provider"),
        "model": model_value,
        "input_contract_digest": input_contract_digest,
    }


def _reservation_body(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    if record.get("kind") != "usage_reservation" or not isinstance(record.get("body"), Mapping):
        return None
    return record["body"]


def _settlement_body(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    if record.get("kind") != "usage_settlement" or not isinstance(record.get("body"), Mapping):
        return None
    return record["body"]


def _reservation_usage(records: list[dict[str, Any]], reservation: Mapping[str, Any]) -> tuple[int, float]:
    """Return reserved caps or its exact settled usage, never an optimistic sum."""
    reservation_digest = reservation.get("content_digest")
    reservation_id = reservation.get("id")
    if not isinstance(reservation_digest, str) or not isinstance(reservation_id, str):
        raise UsageLedgerError("ledger reservation has no digest")
    body = _reservation_body(reservation)
    if body is None:
        raise UsageLedgerError("ledger reservation is malformed")
    settlement: Mapping[str, Any] | None = None
    for record in records:
        candidate = _settlement_body(record)
        if candidate is None:
            continue
        # Envelope content digests intentionally cover the semantic body, not
        # generated identifiers.  Two otherwise identical reservations can
        # therefore share a content digest; the id+digest pair is the stable
        # journal identity for settlement purposes.
        if candidate.get("reservation_digest") == reservation_digest and candidate.get("reservation_id") == reservation_id:
            if settlement is not None:
                raise UsageLedgerError("ledger contains multiple settlements for one reservation")
            settlement = candidate
    if settlement is None:
        return (
            _nonnegative_int(body.get("max_tokens"), "reservation.max_tokens"),
            _nonnegative_number(body.get("max_cost_usd"), "reservation.max_cost_usd"),
        )
    token_in = _nonnegative_int(settlement.get("token_in"), "settlement.token_in")
    token_out = _nonnegative_int(settlement.get("token_out"), "settlement.token_out")
    actual_tokens = _nonnegative_int(settlement.get("actual_tokens"), "settlement.actual_tokens")
    if actual_tokens != token_in + token_out:
        raise UsageLedgerError("settlement actual_tokens does not equal token_in + token_out")
    for field in ("run_id", "work_item_id", "worker", "provider", "model", "input_contract_digest"):
        if settlement.get(field) != body.get(field):
            raise UsageLedgerError(f"settlement does not match its reservation binding: {field}")
    output_digest = settlement.get("output_contract_digest")
    require_digest(output_digest, "settlement.output_contract_digest")
    actual_cost = _nonnegative_number(settlement.get("settled_cost_usd"), "settlement.settled_cost_usd")
    if actual_tokens > _nonnegative_int(body.get("max_tokens"), "reservation.max_tokens") or actual_cost > _nonnegative_number(
        body.get("max_cost_usd"), "reservation.max_cost_usd"
    ):
        raise UsageLedgerError("settlement exceeds its reservation")
    return actual_tokens, actual_cost


def _enforce_budget(
    records: list[dict[str, Any]],
    *,
    scope: Mapping[str, str],
    max_tokens: int,
    max_cost_usd: float,
    budget: UsageBudget,
) -> None:
    """Validate the exact shared budget and reserve pessimistically under lock."""
    run_id = scope["run_id"]
    worker = scope["worker"]
    if worker not in budget.role_max_tokens:
        raise UsageLedgerError(f"usage budget has no role cap for {worker}")

    total_tokens = max_tokens
    total_cost = max_cost_usd
    role_tokens = max_tokens
    role_cost = max_cost_usd
    for existing in records:
        body = _reservation_body(existing)
        if body is None or body.get("run_id") != run_id:
            continue
        existing_budget = body.get("budget")
        existing_budget_digest = body.get("budget_digest")
        if not isinstance(existing_budget, Mapping) or not isinstance(existing_budget_digest, str):
            raise UsageLedgerError("run contains a reservation without an immutable budget binding")
        if digest(dict(existing_budget)) != existing_budget_digest:
            raise UsageLedgerError("ledger reservation budget digest is invalid")
        if existing_budget_digest != budget.content_digest:
            raise UsageLedgerError("run usage budget differs from its first reservation")
        tokens, cost = _reservation_usage(records, existing)
        total_tokens += tokens
        total_cost += cost
        if body.get("worker") == worker:
            role_tokens += tokens
            role_cost += cost

    if total_tokens > budget.total_max_tokens or total_cost > budget.total_max_cost_usd:
        raise UsageBudgetExceededError("usage reservation exceeds total run budget")
    if role_tokens > budget.role_max_tokens[worker] or role_cost > budget.role_max_cost_usd[worker]:
        raise UsageBudgetExceededError(f"usage reservation exceeds role budget for {worker}")


def append_event(paths: WorkspacePaths, event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    record = make_envelope(
        kind="event",
        identifier=f"event-{secrets.token_hex(12)}",
        status="recorded",
        body={"event_type": _require_text(event_type, "event_type"), "payload": dict(payload)},
    )
    _append_jsonl(paths.events, record)
    return record


def append_usage(
    paths: WorkspacePaths,
    *,
    worker: str,
    model: str,
    input_contract_digest: str,
    token_in: int,
    token_out: int,
    reserved_cost_usd: float,
    settled_cost_usd: float,
    latency_ms: float,
    outcome: str,
) -> dict[str, Any]:
    """Append a legacy informational usage record.

    New execution must use :func:`reserve_usage` and :func:`settle_usage`.
    This compatibility record is deliberately not counted as a reservation.
    """
    scope = _legacy_scope(worker=worker, model=model, input_contract_digest=input_contract_digest)
    record = make_envelope(
        kind="usage",
        identifier=f"usage-{secrets.token_hex(12)}",
        status=_require_text(outcome, "outcome"),
        body={
            **scope,
            "token_in": _nonnegative_int(token_in, "token_in"),
            "token_out": _nonnegative_int(token_out, "token_out"),
            "reserved_cost_usd": _nonnegative_number(reserved_cost_usd, "reserved_cost_usd"),
            "settled_cost_usd": _nonnegative_number(settled_cost_usd, "settled_cost_usd"),
            "latency_ms": _nonnegative_number(latency_ms, "latency_ms"),
        },
    )
    _append_jsonl(paths.usage, record)
    return record


def reserve_usage(
    paths: WorkspacePaths,
    *,
    worker: str,
    model: str,
    input_contract_digest: str,
    max_tokens: int,
    max_cost_usd: float,
    reservation_id: str | None = None,
    run_id: str | None = None,
    work_item_id: str | None = None,
    provider: str | None = None,
    budget: UsageBudget | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Reserve bounded usage before execution.

    When a ``budget`` is supplied the check and append share one OS lock.  The
    reservation stores the complete run/work-item/role/model/input binding;
    settlement later adds the canonical output digest and actual usage.
    """
    paths.ensure()
    token_cap = _nonnegative_int(max_tokens, "max_tokens")
    cost_cap = _nonnegative_number(max_cost_usd, "max_cost_usd")
    scope = _scope(
        worker=worker,
        model=model,
        input_contract_digest=input_contract_digest,
        run_id=run_id,
        work_item_id=work_item_id,
        provider=provider,
    )
    resolved_budget = UsageBudget.from_value(budget) if budget is not None else None
    body: dict[str, Any] = {
        **scope,
        "max_tokens": token_cap,
        "max_cost_usd": cost_cap,
        # An output cannot exist at reservation time.  Its exact canonical
        # digest is mandatory on the corresponding settlement record.
        "output_contract_digest": None,
    }
    if resolved_budget is not None:
        body["budget"] = resolved_budget.as_dict()
        body["budget_digest"] = resolved_budget.content_digest
    record = make_envelope(
        kind="usage_reservation",
        identifier=reservation_id or f"usage-reservation-{secrets.token_hex(12)}",
        status="reserved",
        body=body,
    )
    with _exclusive_ledger(paths.usage) as fd:
        records = _usage_records_fd(fd)
        if any(existing.get("id") == record["id"] for existing in records):
            raise UsageLedgerError("usage reservation id already exists in the append-only ledger")
        if resolved_budget is not None:
            _enforce_budget(
                records,
                scope=scope,
                max_tokens=token_cap,
                max_cost_usd=cost_cap,
                budget=resolved_budget,
            )
        _append_jsonl_fd(fd, record)
    return record


def settle_usage(
    paths: WorkspacePaths,
    *,
    reservation: Mapping[str, Any],
    token_in: int,
    token_out: int,
    settled_cost_usd: float,
    latency_ms: float,
    outcome: str,
    output_contract_digest: str | None = None,
    cost_accounting: str = "provider_reported",
) -> dict[str, Any]:
    """Append exactly one bounded settlement for a ledger-member reservation."""
    paths.ensure()
    if not isinstance(reservation, Mapping):
        raise UsageLedgerError("usage reservation must be an envelope")
    try:
        reservation_envelope = verify_envelope(reservation, kind="usage_reservation")
    except Exception as exc:
        raise UsageLedgerError(f"invalid usage reservation envelope: {exc}") from exc
    if reservation_envelope.get("status") != "reserved":
        raise UsageLedgerError("usage reservation is not in reserved state")
    body = reservation_envelope["body"]
    if not isinstance(body, Mapping):
        raise UsageLedgerError("usage reservation body is malformed")
    required_scope = ("run_id", "work_item_id", "worker", "provider", "model", "input_contract_digest")
    scope = {key: _require_text(body.get(key), f"reservation.{key}") for key in required_scope if key != "input_contract_digest"}
    input_digest = body.get("input_contract_digest")
    require_digest(input_digest, "reservation.input_contract_digest")
    scope["input_contract_digest"] = input_digest
    max_tokens = _nonnegative_int(body.get("max_tokens"), "reservation.max_tokens")
    max_cost = _nonnegative_number(body.get("max_cost_usd"), "reservation.max_cost_usd")
    input_tokens = _nonnegative_int(token_in, "token_in")
    output_tokens = _nonnegative_int(token_out, "token_out")
    actual_cost = _nonnegative_number(settled_cost_usd, "settled_cost_usd")
    accounting = _require_text(cost_accounting, "cost_accounting")
    if accounting not in {"provider_reported", "reservation_upper_bound", "zero_cost_fixture", "legacy_unspecified"}:
        raise UsageLedgerError("cost_accounting is not recognized")
    latency = _nonnegative_number(latency_ms, "latency_ms")
    if input_tokens + output_tokens > max_tokens or actual_cost > max_cost:
        raise UsageLedgerError("usage settlement exceeds reservation")
    actual_output_digest = output_contract_digest or digest({"kind": "legacy-no-output"})
    require_digest(actual_output_digest, "output_contract_digest")
    record = make_envelope(
        kind="usage_settlement",
        identifier=f"usage-settlement-{secrets.token_hex(12)}",
        status=_require_text(outcome, "outcome"),
        body={
            "reservation_id": reservation_envelope["id"],
            "reservation_digest": reservation_envelope["content_digest"],
            **scope,
            "output_contract_digest": actual_output_digest,
            "token_in": input_tokens,
            "token_out": output_tokens,
            "actual_tokens": input_tokens + output_tokens,
            "settled_cost_usd": actual_cost,
            "cost_accounting": accounting,
            "latency_ms": latency,
        },
    )
    # Ledger membership, double-settlement detection, and append must be one
    # critical section.  Otherwise two processes can both see it unsettled.
    with _exclusive_ledger(paths.usage) as fd:
        records = _usage_records_fd(fd)
        present = any(
            existing.get("kind") == "usage_reservation"
            and existing.get("id") == reservation_envelope["id"]
            and existing.get("content_digest") == reservation_envelope["content_digest"]
            and canonical_json(existing) == canonical_json(reservation_envelope)
            for existing in records
        )
        if not present:
            raise UsageLedgerError("usage reservation is not present in the append-only ledger")
        if any(
            settlement is not None
            and settlement.get("reservation_digest") == reservation_envelope["content_digest"]
            and settlement.get("reservation_id") == reservation_envelope["id"]
            for settlement in (_settlement_body(existing) for existing in records)
        ):
            raise UsageLedgerError("usage reservation has already been settled")
        _append_jsonl_fd(fd, record)
    return record


def mark_usage_unsettled(
    paths: WorkspacePaths,
    *,
    reservation: Mapping[str, Any],
    reason: str,
) -> dict[str, Any]:
    """Record a sent provider call whose actual usage cannot be recovered.

    The reservation deliberately remains un-settled and therefore continues to
    consume its full approved cap in future budget checks.  This avoids both a
    fictitious zero-usage settlement and an unsafe release of spend capacity.
    """

    paths.ensure()
    if not isinstance(reservation, Mapping):
        raise UsageLedgerError("usage reservation must be an envelope")
    try:
        reservation_envelope = verify_envelope(reservation, kind="usage_reservation")
    except Exception as exc:
        raise UsageLedgerError(f"invalid usage reservation envelope: {exc}") from exc
    body = reservation_envelope.get("body")
    if not isinstance(body, Mapping):
        raise UsageLedgerError("usage reservation body is malformed")
    reason_value = _require_text(reason, "unsettled usage reason")[:512]
    record = make_envelope(
        kind="usage_unsettled",
        identifier=f"usage-unsettled-{secrets.token_hex(12)}",
        status="unknown_unsettled",
        body={
            "reservation_id": reservation_envelope["id"],
            "reservation_digest": reservation_envelope["content_digest"],
            "run_id": body.get("run_id"),
            "work_item_id": body.get("work_item_id"),
            "worker": body.get("worker"),
            "provider": body.get("provider"),
            "model": body.get("model"),
            "input_contract_digest": body.get("input_contract_digest"),
            "reason": reason_value,
        },
    )
    with _exclusive_ledger(paths.usage) as fd:
        records = _usage_records_fd(fd)
        present = any(
            existing.get("kind") == "usage_reservation"
            and existing.get("id") == reservation_envelope["id"]
            and existing.get("content_digest") == reservation_envelope["content_digest"]
            and canonical_json(existing) == canonical_json(reservation_envelope)
            for existing in records
        )
        if not present:
            raise UsageLedgerError("usage reservation is not present in the append-only ledger")
        for existing in records:
            settled = _settlement_body(existing)
            if (
                settled is not None
                and settled.get("reservation_id") == reservation_envelope["id"]
                and settled.get("reservation_digest") == reservation_envelope["content_digest"]
            ):
                raise UsageLedgerError("usage reservation has already been settled")
            unsettled = existing.get("body") if existing.get("kind") == "usage_unsettled" else None
            if (
                isinstance(unsettled, Mapping)
                and unsettled.get("reservation_id") == reservation_envelope["id"]
                and unsettled.get("reservation_digest") == reservation_envelope["content_digest"]
            ):
                raise UsageLedgerError("usage reservation is already marked unsettled")
        _append_jsonl_fd(fd, record)
    return record
