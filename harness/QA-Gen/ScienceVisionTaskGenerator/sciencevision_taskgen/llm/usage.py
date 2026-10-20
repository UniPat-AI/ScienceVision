"""Append-only JSONL usage log for LLM calls in the ScienceVision pipeline.

Immutability guarantee: ``UsageLog.record()`` returns a NEW ``UsageLog``
instance; the original is never mutated.  Each call also appends a JSON line
to the on-disk log immediately, so partial runs leave a usable log.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class UsageRecord:
    """Immutable record of a single LLM call."""

    stage: str          # "builder", "challenger", or "judge"
    task_id: str
    v_class: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    latency_ms: float
    cost_usd: float
    fallback: bool       # True when the LLM call failed and a template/rule fallback was used
    error: str | None    # set only when fallback=True


class UsageLog:
    """Append-only JSONL log stored under ``<run_dir>/llm_usage.jsonl``.

    Construction::

        log = UsageLog.for_run(run_dir, pricing)

    Adding a record (returns a **new** ``UsageLog``)::

        log = log.record("builder", task_id, v_class, model,
                         prompt_tokens=100, completion_tokens=50, ...)

    The class is effectively immutable: ``record()`` never mutates ``self``.
    """

    def __init__(
        self,
        log_path: Path,
        pricing: dict[str, dict[str, float]],
        records: tuple[UsageRecord, ...] = (),
    ) -> None:
        # Use object.__setattr__ so subclasses could freeze if desired; here
        # we rely on convention — callers should not mutate these attributes.
        object.__setattr__(self, "_log_path", log_path)
        object.__setattr__(self, "_pricing", {k: dict(v) for k, v in pricing.items()})
        object.__setattr__(self, "_records", records)

    # ------------------------------------------------------------------
    # Class-level constructor
    # ------------------------------------------------------------------

    @classmethod
    def for_run(
        cls,
        run_dir: Path,
        pricing: dict[str, dict[str, float]],
    ) -> "UsageLog":
        """Create (or resume) a ``UsageLog`` for *run_dir*.

        Creates *run_dir* if it does not exist.  Does NOT replay an existing
        ``llm_usage.jsonl`` file — this is a write-forward log.
        """
        run_dir.mkdir(parents=True, exist_ok=True)
        log_path = run_dir / "llm_usage.jsonl"
        return cls(log_path, pricing)

    # ------------------------------------------------------------------
    # Core mutation (returns new instance)
    # ------------------------------------------------------------------

    def record(
        self,
        stage: str,
        task_id: str,
        v_class: str,
        model: str,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        reasoning_tokens: int,
        latency_ms: float,
        fallback: bool = False,
        error: str | None = None,
    ) -> "UsageLog":
        """Append a record and return a NEW ``UsageLog``.

        Cost is computed from ``pricing[model]`` (keys ``'in'`` and ``'out'``
        are prices per million tokens).  If the model is not in pricing, cost
        is 0.0.
        """
        pricing_entry = self._pricing.get(model, {})  # type: ignore[attr-defined]
        in_per_mtok: float = pricing_entry.get("in", 0.0)
        out_per_mtok: float = pricing_entry.get("out", 0.0)
        cost_usd = (
            prompt_tokens * in_per_mtok + completion_tokens * out_per_mtok
        ) / 1_000_000.0

        rec = UsageRecord(
            stage=stage,
            task_id=task_id,
            v_class=v_class,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            reasoning_tokens=reasoning_tokens,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            fallback=fallback,
            error=error,
        )

        # Atomic-ish append: open in append mode, write one JSON line, flush.
        log_path: Path = self._log_path  # type: ignore[attr-defined]
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(rec)) + "\n")
            fh.flush()

        old_records: tuple[UsageRecord, ...] = self._records  # type: ignore[attr-defined]
        return UsageLog(log_path, self._pricing, old_records + (rec,))  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Read access
    # ------------------------------------------------------------------

    def all_records(self) -> tuple[UsageRecord, ...]:
        """Return all in-memory records as an immutable tuple."""
        return self._records  # type: ignore[attr-defined]
