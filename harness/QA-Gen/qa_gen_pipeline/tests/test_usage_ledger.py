from __future__ import annotations

import json
import multiprocessing
from pathlib import Path

import pytest

from qagen.contracts import digest
from qagen.paths import WorkspacePaths
from qagen.storage.ledger import UsageBudget, UsageBudgetExceededError, UsageLedgerError, reserve_usage, settle_usage


def _budget(*, total_tokens: int = 10, author_tokens: int = 6) -> UsageBudget:
    return UsageBudget(
        total_max_tokens=total_tokens,
        total_max_cost_usd=10.0,
        role_max_tokens={"author": author_tokens, "verifier": 8},
        role_max_cost_usd={"author": 6.0, "verifier": 8.0},
    )


def _reserve_in_child(workspace: str, queue: multiprocessing.Queue[object]) -> None:
    paths = WorkspacePaths.from_value(workspace)
    try:
        reserve_usage(
            paths,
            run_id="run-concurrent",
            work_item_id="item-concurrent",
            worker="author",
            provider="fixture-author",
            model="fixture-model",
            input_contract_digest=digest({"child": True}),
            max_tokens=6,
            max_cost_usd=1.0,
            budget=_budget(total_tokens=10, author_tokens=10),
        )
    except Exception as exc:  # The parent asserts the exact one-success outcome.
        queue.put(type(exc).__name__)
    else:
        queue.put("ok")


def _reserve(
    paths: WorkspacePaths,
    *,
    reservation_id: str = "reserve-a",
    max_tokens: int = 6,
    budget: UsageBudget | None = None,
) -> dict[str, object]:
    return reserve_usage(
        paths,
        run_id="run-a",
        work_item_id="item-a",
        worker="author",
        provider="fixture-author",
        model="fixture-model",
        input_contract_digest=digest({"input": "a"}),
        max_tokens=max_tokens,
        max_cost_usd=1.0,
        reservation_id=reservation_id,
        budget=budget or _budget(),
    )


def test_reservation_and_settlement_bind_full_execution_identity(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    reservation = _reserve(paths)
    output_digest = digest({"candidate": "a"})

    settlement = settle_usage(
        paths,
        reservation=reservation,
        token_in=2,
        token_out=3,
        settled_cost_usd=0.4,
        latency_ms=8.0,
        outcome="completed",
        output_contract_digest=output_digest,
    )

    reservation_body = reservation["body"]
    settlement_body = settlement["body"]
    for field in ("run_id", "work_item_id", "worker", "provider", "model", "input_contract_digest"):
        assert settlement_body[field] == reservation_body[field]
    assert settlement_body["reservation_id"] == reservation["id"]
    assert settlement_body["reservation_digest"] == reservation["content_digest"]
    assert settlement_body["output_contract_digest"] == output_digest
    assert settlement_body["actual_tokens"] == 5

    with pytest.raises(UsageLedgerError, match="already been settled"):
        settle_usage(
            paths,
            reservation=reservation,
            token_in=0,
            token_out=0,
            settled_cost_usd=0.0,
            latency_ms=1.0,
            outcome="completed",
            output_contract_digest=output_digest,
        )


def test_reservation_is_a_ledger_member_and_ids_are_unique(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    reservation = _reserve(paths, reservation_id="fixed-id")

    with pytest.raises(UsageLedgerError, match="id already exists"):
        _reserve(paths, reservation_id="fixed-id")

    other_paths = WorkspacePaths.from_value(tmp_path / "other-workspace")
    with pytest.raises(UsageLedgerError, match="not present"):
        settle_usage(
            other_paths,
            reservation=reservation,
            token_in=0,
            token_out=0,
            settled_cost_usd=0.0,
            latency_ms=1.0,
            outcome="failed",
            output_contract_digest=digest({"failed": True}),
        )


def test_total_and_role_budget_use_actual_settled_usage(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    budget = _budget(total_tokens=20, author_tokens=10)
    first = _reserve(paths, reservation_id="first", max_tokens=6, budget=budget)
    settle_usage(
        paths,
        reservation=first,
        token_in=1,
        token_out=1,
        settled_cost_usd=0.1,
        latency_ms=1.0,
        outcome="completed",
        output_contract_digest=digest({"first": True}),
    )

    # The settled reservation counts as two actual tokens, not its six-token cap.
    second = _reserve(paths, reservation_id="second", max_tokens=6, budget=budget)
    assert second["body"]["budget_digest"] == first["body"]["budget_digest"]

    with pytest.raises(UsageBudgetExceededError, match="role budget"):
        _reserve(paths, reservation_id="third", max_tokens=3, budget=budget)

    raw = [json.loads(line) for line in paths.usage.read_text(encoding="utf-8").splitlines()]
    assert [record["kind"] for record in raw] == ["usage_reservation", "usage_settlement", "usage_reservation"]


def test_os_lock_makes_concurrent_budget_reservation_atomic(tmp_path: Path) -> None:
    try:
        context = multiprocessing.get_context("fork")
    except ValueError:  # pragma: no cover - the supported local CI platforms provide fork.
        pytest.skip("fork process context is unavailable")
    workspace = str(tmp_path / "workspace")
    queue: multiprocessing.Queue[object] = context.Queue()
    workers = [context.Process(target=_reserve_in_child, args=(workspace, queue)) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)
        assert worker.exitcode == 0
    outcomes = sorted(str(queue.get(timeout=2)) for _ in workers)

    assert outcomes == ["UsageBudgetExceededError", "ok"]
