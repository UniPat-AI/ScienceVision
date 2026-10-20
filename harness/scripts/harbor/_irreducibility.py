"""Label each task by *what it takes* to answer it — the T0 irreducibility probe.

A benchmark of 3D perception is only measuring 3D perception on the tasks where
no cheaper route reaches the answer. Two probe routes establish that per task:

    codex-text  the question prose alone; no file, no image, no viewer, no MCP
    cc          the raw 3D file plus Bash; no MCP, no renders

and the task gets exactly one label:

    text-leaked       prose alone sufficed. The question gives the answer away,
                      or the answer is memorised world knowledge. Not citable.
    file-reducible    prose failed, file computation succeeded. The agent never
                      perceived anything — it ran numpy over the staged array.
                      Not citable as a perception result.
    discriminative    neither shortcut worked. This is the citable set.
    unknown           a probe is missing. Never silently promoted.

The concrete motivation: on the 11 scored ``med_ct_geometry`` tasks, ``cc``
scored 0.932 with ``process_rendered = 0.00`` — it looked at no image at all and
computed centroids off the staged ground-truth mask. Those same tasks carry the
largest cc-vs-cc2d gap on the EV2 leaderboard, so the headline "3D access helps"
number is partly manufactured by a shortcut. Until T0 has run, that row is not
citable, and this module is what produces the evidence to say so.

Two design rules worth stating, because both are easy to get subtly wrong:

* ``text-leaked`` outranks ``file-reducible``. Prose leakage is the more
  damaging defect and is not diluted by the 3D routes also succeeding.
* ``discriminative`` is *not* "a good task". A task nobody solved also lands
  there. ``solved_by_perception_route`` carries that fact separately so a
  reporting layer can tell "irreducible and answerable" from "irreducible and
  possibly impossible" without inventing a fifth label the doc does not define.

Pure functions only — no I/O, no CLI. The run-walking driver is
``scripts/harbor/probe_irreducibility.py``.
"""
from __future__ import annotations

from typing import Any, Mapping

IRREDUCIBILITY_SCHEMA_VERSION = "sciencevision.irreducibility.v1"

# Matches scripts/qa_qc.py's --shortcut-success default: the score at or above
# which a probe route counts as having answered the task.
DEFAULT_SUCCESS_THRESHOLD = 0.5

LABEL_TEXT_LEAKED = "text-leaked"
LABEL_FILE_REDUCIBLE = "file-reducible"
LABEL_DISCRIMINATIVE = "discriminative"
LABEL_UNKNOWN = "unknown"

ALL_LABELS: tuple[str, ...] = (
    LABEL_TEXT_LEAKED,
    LABEL_FILE_REDUCIBLE,
    LABEL_DISCRIMINATIVE,
    LABEL_UNKNOWN,
)

# Route names must be ones the QC gate already recognises, or attach_probe_results
# falls through both of its branches and attaches nothing at all — a probe that
# reports "clean" having checked nothing. See _qa_qc_shortcuts.TEXT_PROBE_BASELINES
# and RAW_COMPUTE_BASELINES.
TEXT_ROUTE = "codex-text"
FILE_ROUTE = "cc"
PERCEPTION_ROUTE = "cc2d-6"


def effective_score(cell: Mapping[str, Any] | None) -> tuple[float | None, str]:
    """Pick the score to judge a probe route by.

    Prefers ``effective_content_reward`` (answer quality alone) over
    ``effective_reward`` (the weighted total). The weighted total folds in
    process signals that are explicitly diagnostic-only — weight 0.0 on the
    leaderboard — so judging a shortcut by it would mix in a number that is not
    supposed to affect any verdict.
    """
    if not isinstance(cell, Mapping):
        return None, "missing"
    content = cell.get("effective_content_reward")
    if content is not None:
        try:
            return float(content), "content_reward"
        except (TypeError, ValueError):
            pass
    reward = cell.get("effective_reward")
    if reward is not None:
        try:
            return float(reward), "weighted_reward"
        except (TypeError, ValueError):
            pass
    return None, "missing"


def _solved(score: float | None, threshold: float) -> bool | None:
    """Tri-state: None means unmeasured, never 'did not solve it'."""
    return None if score is None else score >= threshold


def label_task(
    *,
    text_cell: Mapping[str, Any] | None,
    file_cell: Mapping[str, Any] | None,
    perception_cell: Mapping[str, Any] | None = None,
    threshold: float = DEFAULT_SUCCESS_THRESHOLD,
) -> dict[str, Any]:
    """Label one task from its per-route probe cells. Returns a new dict."""
    text_score, text_kind = effective_score(text_cell)
    file_score, file_kind = effective_score(file_cell)
    perception_score, perception_kind = effective_score(perception_cell)

    text_solved = _solved(text_score, threshold)
    file_solved = _solved(file_score, threshold)

    if text_solved:
        label = LABEL_TEXT_LEAKED
        reason = f"text probe scored {text_score:.3f} >= {threshold:.2f}"
    elif file_solved:
        label = LABEL_FILE_REDUCIBLE
        reason = f"file probe scored {file_score:.3f} >= {threshold:.2f}"
        if text_solved is None:
            reason += "; text probe missing, so prose leakage is unmeasured"
    elif text_solved is None:
        label = LABEL_UNKNOWN
        reason = "text probe missing; prose leakage unmeasured"
    elif file_solved is None:
        label = LABEL_UNKNOWN
        reason = "file probe missing; file reducibility unmeasured"
    else:
        label = LABEL_DISCRIMINATIVE
        reason = (
            f"neither shortcut route reached {threshold:.2f} "
            f"(text {text_score:.3f}, file {file_score:.3f})"
        )

    return {
        "irreducibility": label,
        "reason": reason,
        "threshold": threshold,
        "text_score": text_score,
        "text_score_kind": text_kind,
        "file_score": file_score,
        "file_score_kind": file_kind,
        "perception_score": perception_score,
        "perception_score_kind": perception_kind,
        "solved_by_perception_route": _solved(perception_score, threshold),
    }


def _empty_counts() -> dict[str, int]:
    return {label: 0 for label in ALL_LABELS}


def summarize_labels(
    per_task: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Roll per-task labels up to overall and per-domain counts.

    ``n_citable`` counts only ``discriminative`` tasks: both shortcut labels and
    ``unknown`` are excluded, so an unrun probe can never inflate the citable
    set. ``n_discriminative_unsolved`` is reported alongside it because a task no
    route solved is irreducible *and* unvalidated — a reviewer needs both facts.
    """
    by_label = _empty_counts()
    by_domain: dict[str, dict[str, int]] = {}
    n_discriminative_unsolved = 0

    for row in (per_task or {}).values():
        labels = row.get("labels") if isinstance(row, Mapping) else None
        label = str((labels or {}).get("irreducibility") or LABEL_UNKNOWN)
        if label not in by_label:
            label = LABEL_UNKNOWN
        by_label[label] += 1

        domain = str((row or {}).get("domain") or "unknown")
        by_domain.setdefault(domain, _empty_counts())[label] += 1

        if label == LABEL_DISCRIMINATIVE and (labels or {}).get(
            "solved_by_perception_route"
        ) is False:
            n_discriminative_unsolved += 1

    return {
        "n": len(per_task or {}),
        "by_label": by_label,
        "by_domain": by_domain,
        "n_citable": by_label[LABEL_DISCRIMINATIVE],
        "n_unknown": by_label[LABEL_UNKNOWN],
        "n_discriminative_unsolved": n_discriminative_unsolved,
    }
