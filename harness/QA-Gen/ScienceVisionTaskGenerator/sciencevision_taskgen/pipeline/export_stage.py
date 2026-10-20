"""Output writing and run reporting."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from sciencevision_taskgen.stats import stats_to_markdown, summarize_run
from sciencevision_taskgen.utils import TASKGEN_ROOT, save_json, save_jsonl


def is_release_ready(task: dict[str, Any]) -> bool:
    """Return True iff the task has verified ground_truth AND a passing release_gate."""
    gt_status = task.get("ground_truth", {}).get("status")
    gate = task.get("validation_record", {}).get("release_gate")
    return gt_status in {"expert_verified", "released"} and gate == "pass"


class ExportStage:
    """Writes run artifacts."""

    def export(
        self,
        run_dir: Path,
        catalog: dict[str, Any],
        plan: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
        critiques: list[dict[str, Any]],
        decisions: list[dict[str, Any]],
        final_tasks: list[dict[str, Any]],
    ) -> dict[str, str]:
        release_ready = [t for t in final_tasks if is_release_ready(t)]

        save_json(catalog.get("summary", {}), run_dir / "catalog_summary.json")
        save_json(plan, run_dir / "seed_plan.json")
        save_json(candidates, run_dir / "candidate_tasks.json")
        save_json(critiques, run_dir / "critiques.json")
        save_json(decisions, run_dir / "decisions.json")
        save_json(final_tasks, run_dir / "final_tasks.json")
        save_jsonl(final_tasks, run_dir / "final_tasks.jsonl")

        seed_jsonl_path = run_dir / f"{run_dir.name}_seed_tasks.jsonl"
        release_ready_jsonl_path = run_dir / f"{run_dir.name}_release_ready_tasks.jsonl"
        save_jsonl(final_tasks, seed_jsonl_path)
        save_jsonl(release_ready, release_ready_jsonl_path)

        shared_dir = TASKGEN_ROOT / "data" / "final_tasks"
        shared_jsonl = shared_dir / f"{run_dir.name}_tasks.jsonl"
        save_jsonl(final_tasks, shared_jsonl)

        report = self._build_report(catalog, plan, candidates, critiques, decisions, final_tasks, release_ready)
        report_path = run_dir / "REPORT.md"
        report_path.write_text(report, encoding="utf-8")
        stats = summarize_run(run_dir)
        save_json(stats, run_dir / "generation_stats.json")
        (run_dir / "GENERATION_STATS.md").write_text(stats_to_markdown(stats), encoding="utf-8")

        return {
            "run_dir": str(run_dir),
            "final_json": str(run_dir / "final_tasks.json"),
            "final_jsonl": str(run_dir / "final_tasks.jsonl"),
            "seed_jsonl": str(seed_jsonl_path),
            "release_ready_jsonl": str(release_ready_jsonl_path),
            "shared_final_jsonl": str(shared_jsonl),
            "report": str(report_path),
            "generation_stats_json": str(run_dir / "generation_stats.json"),
            "generation_stats_md": str(run_dir / "GENERATION_STATS.md"),
        }

    @staticmethod
    def _build_report(
        catalog: dict[str, Any],
        plan: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
        critiques: list[dict[str, Any]],
        decisions: list[dict[str, Any]],
        final_tasks: list[dict[str, Any]],
        release_ready: list[dict[str, Any]],
    ) -> str:
        by_domain = Counter(task["domain"] for task in final_tasks)
        by_v = Counter(task["v_class"] for task in final_tasks)
        plan_status = Counter(item.get("status", "unknown") for item in plan)
        critique_sev = Counter(item.get("severity", "unknown") for item in critiques)
        decision_counts = Counter(item.get("decision", "unknown") for item in decisions)

        lines = [
            "# ScienceVision Task Generation Report",
            "",
            "## Summary",
            "",
            f"- Catalog assets: {catalog.get('summary', {}).get('asset_count', 0)}",
            f"- Matched 3D-grounded QA records: {catalog.get('summary', {}).get('matched_qa_total', 0)}",
            f"- Planned cells/seeds: {len(plan)}",
            f"- Candidate tasks: {len(candidates)}",
            f"- Accepted seed tasks: {len(final_tasks)}",
            f"- Release-ready tasks: {len(release_ready)}",
            "",
            "## Plan Status",
            "",
        ]
        lines.extend(f"- {key}: {value}" for key, value in sorted(plan_status.items()))
        lines.extend(["", "## Decisions", ""])
        lines.extend(f"- {key}: {value}" for key, value in sorted(decision_counts.items()))
        lines.extend(["", "## Critique Severity", ""])
        lines.extend(f"- {key}: {value}" for key, value in sorted(critique_sev.items()))
        lines.extend(["", "## Final Coverage By Domain", ""])
        lines.extend(f"- {key}: {value}" for key, value in sorted(by_domain.items()))
        lines.extend(["", "## Final Coverage By V-Class", ""])
        lines.extend(f"- {key}: {value}" for key, value in sorted(by_v.items()))
        lines.extend(["", "## Notes", ""])
        lines.append("- Generated tasks are seed specs. Ground-truth values marked `needs_expert_annotation` must be filled with rendered or expert labels before benchmark release.")
        lines.append("- V6 tasks include forbidden-tool contracts; the evaluator must enforce them during agent runs.")
        if not release_ready:
            lines.extend(["", "## Release Gate", ""])
            lines.append(
                "- 0 release-ready: accepted tasks have unverified ground_truth"
                " (e.g. needs_expert_annotation) and/or validation_record.release_gate != \"pass\"."
                " Expert verification is required before release."
            )
        return "\n".join(lines) + "\n"
