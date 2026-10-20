"""Refuse to cite a number the process gate could not certify.

``_process_gate`` already decides, per run, whether its evidence is clean. This
module is the reporting half of that judgement, and without it the verdict is a
column nobody acts on: every leaderboard keeps averaging all eligible trials, so
a ``cc`` run that computed its answer off the staged ground-truth mask still
enters the headline as 0.932.

Three states, and collapsing any two of them is the bug this module exists to
prevent:

    True    the gate ran and found no blocker. Counts toward the headline.
    False   the gate ran and a blocker fired. Excluded, counted as *blocked*.
    None    the gate never scored this run -- archived before it existed, or a
            row built by a caller that does not carry the verdict. Excluded too,
            but counted separately: "we did not look" and "we looked and it
            failed" are different facts about a run, and a report that merges
            them cannot tell a hardened baseline from an unexamined one.

The headline rule is therefore ``citable is True``. The direction is deliberate:
a run nobody certified is not evidence, so it stays out. Every caller must
render the census beside the number -- a filtered mean that does not say how
many trials it dropped is indistinguishable from a lower score.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence, TypeVar

#: The process-reward key that carries the gate's verdict. Emitted by
#: ``harbor_adapters.sci3d._process_gate`` so the verdict travels in Harbor's
#: reward channel, which is the only thing a plain ``result.json`` reader sees.
CITABLE_KEY = "process_citable"

T = TypeVar("T")


def citability(rewards: Mapping[str, Any] | None) -> bool | None:
    """The tri-state verdict carried by one run's rewards dict.

    A missing key is ``None`` ("ungated"), never ``False``. Reward dicts are
    flat floats and a consumer defaulting absent to ``0.0`` would report every
    pre-gate run as a finding.
    """
    value = (rewards or {}).get(CITABLE_KEY)
    if value is None:
        return None
    return bool(value)


@dataclass(frozen=True)
class Census:
    """How an eligible set split by citability.

    ``ungated`` is not a failure count. It is the number of trials on which the
    gate had no opinion, and it is the figure that tells a reader whether the
    headline denominator is smaller because trials were blocked or because they
    were never examined.
    """

    citable: int
    blocked: int
    ungated: int

    @property
    def eligible(self) -> int:
        return self.citable + self.blocked + self.ungated

    def as_dict(self) -> dict[str, int]:
        return {
            "n_eligible": self.eligible,
            "n_citable": self.citable,
            "n_blocked": self.blocked,
            "n_ungated": self.ungated,
        }

    def note(self) -> str:
        """One line a report can print without further arithmetic."""
        if self.eligible == self.citable:
            return f"all {self.eligible} eligible trials are citable"
        parts = [f"{self.citable}/{self.eligible} eligible trials are citable"]
        if self.blocked:
            parts.append(f"{self.blocked} blocked by the process gate")
        if self.ungated:
            parts.append(f"{self.ungated} never gated")
        return "; ".join(parts)


def headline_rows(
    rows: Iterable[T],
    verdict: Callable[[T], bool | None],
) -> tuple[list[T], Census]:
    """Split ``rows`` into the citable headline set and a census of the rest.

    ``verdict`` is passed in rather than assumed, because the two callers hold
    the fact in different shapes: a collector row carries the audit's tri-state
    ``citable`` field, while a raw trial carries only its rewards dict. Both
    come from the same ``evaluate_process_gate`` call, so they cannot disagree.
    """
    kept: list[T] = []
    citable = blocked = ungated = 0
    for row in rows:
        state = verdict(row)
        if state is True:
            citable += 1
            kept.append(row)
        elif state is False:
            blocked += 1
        else:
            ungated += 1
    return kept, Census(citable=citable, blocked=blocked, ungated=ungated)


def rows_by_rewards(
    rows: Sequence[T],
    rewards_of: Callable[[T], Mapping[str, Any] | None],
) -> tuple[list[T], Census]:
    """Convenience wrapper for callers whose rows carry a rewards dict."""
    return headline_rows(rows, lambda row: citability(rewards_of(row)))
