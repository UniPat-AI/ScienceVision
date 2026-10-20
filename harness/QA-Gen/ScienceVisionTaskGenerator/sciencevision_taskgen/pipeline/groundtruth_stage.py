"""GroundTruthStage: dispatch FILLERS over a list of tasks.

Pure function-style: never mutates the input list or its dicts.  Always
returns a new list of new dicts and a StageReport summary.

Evidence-path normalization: when ``repo_root`` is supplied, any
absolute ``evidence_path`` that resolves to a location inside
``repo_root`` is converted to a forward-slash repo-relative string.
Paths outside ``repo_root`` and paths that are already relative are
left unchanged.  ``FillResult`` itself is never modified; normalization
happens only when writing into the output task dict.
"""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sciencevision_taskgen.groundtruth import dispatch
from sciencevision_taskgen.groundtruth.base import FillResult


@dataclass(frozen=True)
class StageReport:
    total: int
    auto_seed: int
    awaiting_expert: int
    needs_expert_annotation: int = 0


class GroundTruthStage:
    """Orchestrates the per-task dispatch.

    Args:
        repo_root: Optional root of the repository.  When provided, absolute
            evidence paths that resolve inside ``repo_root`` are normalized to
            forward-slash repo-relative strings before being written into the
            output task dict.  Paths outside ``repo_root`` and already-relative
            paths are kept as-is.
    """

    def __init__(self, repo_root: Path | None = None) -> None:
        self._repo_root: Path | None = (
            Path(repo_root).resolve() if repo_root is not None else None
        )

    def fill_tasks(
        self, tasks: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], StageReport]:
        out: list[dict[str, Any]] = []
        auto_seed = 0
        awaiting_expert = 0
        needs_expert = 0

        for t in tasks:
            updated = copy.deepcopy(t)
            result = dispatch(t)
            self._apply(updated, result, self._repo_root)
            out.append(updated)

            if result.status == "auto_seed":
                auto_seed += 1
            elif result.status == "awaiting_expert":
                awaiting_expert += 1
            else:
                needs_expert += 1

        return out, StageReport(
            total=len(tasks),
            auto_seed=auto_seed,
            awaiting_expert=awaiting_expert,
            needs_expert_annotation=needs_expert,
        )

    @staticmethod
    def _normalize_evidence(
        paths: tuple[str, ...], repo_root: Path | None
    ) -> list[str]:
        """Return evidence paths normalized to repo-relative forward-slash strings.

        Only absolute paths that resolve inside ``repo_root`` are rewritten.
        Relative paths and paths outside ``repo_root`` are returned unchanged.
        """
        if repo_root is None:
            return list(paths)

        result: list[str] = []
        for p in paths:
            if not os.path.isabs(p):
                result.append(p)
                continue
            try:
                rel = Path(p).resolve().relative_to(repo_root)
                result.append(rel.as_posix())
            except ValueError:
                # Path resolves outside repo_root; keep as-is
                result.append(p)
        return result

    @staticmethod
    def _apply(
        task: dict[str, Any], result: FillResult, repo_root: Path | None
    ) -> None:
        gt = task.setdefault("ground_truth", {})
        gt["value"] = result.value
        gt["status"] = result.status
        gt["notes"] = result.notes
        gt["evidence_paths"] = GroundTruthStage._normalize_evidence(
            result.evidence_paths, repo_root
        )
        gt["model"] = result.model

        if result.status == "auto_seed":
            vr = task.setdefault("validation_record", {})
            ac = vr.setdefault("auto_checks", {})
            ac["ground_truth_verified"] = True
