"""Report rendering and persistence for :mod:`scripts.qa_qc`."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [f"# {report['suite']}", "", "## Summary", ""]
    lines.append(f"- Tasks: **{summary['task_count']}**")
    lines.append(f"- Eval attempts: **{summary['eval_attempt_count']}**")
    lines.append(f"- Grades: `{json.dumps(summary['grades'], sort_keys=True)}`")
    lines.append(f"- Decisions: `{json.dumps(summary['decisions'], sort_keys=True)}`")
    lines.append(f"- Findings: `{json.dumps(summary['findings_by_severity'], sort_keys=True)}`")
    lines.append(f"- Labels: `{json.dumps(summary['labels'], sort_keys=True)}`")
    lines.extend(["", "## Eval coverage", "", "| baseline | observed | expected |", "|---|---:|---:|"])
    if summary["eval_coverage"]:
        for baseline, cell in summary["eval_coverage"].items():
            lines.append(f"| `{baseline}` | {cell['observed']} | {cell['expected']} |")
    else:
        lines.append("| _(static only)_ | 0 | 0 |")

    lines.extend(
        [
            "",
            "## Family classification",
            "",
            "| family | tasks | grades | labels |",
            "|---|---:|---|---|",
        ]
    )
    for family, cell in summary["by_family"].items():
        lines.append(
            f"| `{family}` | {cell['task_count']} | "
            f"`{json.dumps(cell['grades'], sort_keys=True)}` | "
            f"`{json.dumps(cell['labels'], sort_keys=True)}` |"
        )

    lines.extend(["", "## Suite findings", "", "| severity | code | finding |", "|---|---|---|"])
    if report["suite_findings"]:
        for finding in report["suite_findings"]:
            lines.append(f"| {finding['severity']} | `{finding['code']}` | {finding['message']} |")
    else:
        lines.append("| info | - | No suite-level findings. |")

    shortcut_tasks = [
        result for result in report["tasks"]
        if any(result.get("shortcuts", {}).get("routes", {}).values())
        or result.get("shortcuts", {}).get("probe_results")
    ]
    lines.extend(["", "## Shortcut audit", ""])
    lines.append(
        "Static candidates are review signals; a successful isolated probe is empirical confirmation."
    )
    lines.extend(["", "| route | candidate tasks |", "|---|---:|"])
    for route in ("text_knowledge", "asset_identifiers", "metadata", "package_compute"):
        count = sum(bool(result["shortcuts"]["routes"].get(route)) for result in shortcut_tasks)
        lines.append(f"| `{route}` | {count} |")
    if shortcut_tasks:
        lines.extend(
            [
                "",
                "| task | routes | declared controls | enforced controls | probes |",
                "|---|---|---:|---:|---|",
            ]
        )
        for result in shortcut_tasks[:50]:
            shortcut = result["shortcuts"]
            routes = ", ".join(name for name, values in shortcut["routes"].items() if values)
            declared = sum(
                bool(route.get("matched_declared_controls"))
                for name in ("metadata", "package_compute")
                for route in shortcut["routes"].get(name, [])
            )
            enforced = sum(
                bool(route.get("matched_enforced_controls"))
                for name in ("metadata", "package_compute")
                for route in shortcut["routes"].get(name, [])
            )
            total = sum(len(shortcut["routes"].get(name, [])) for name in ("metadata", "package_compute"))
            probes = ", ".join(shortcut.get("probe_results", {})) or "-"
            lines.append(
                f"| `{result['task_id']}` | {routes or '-'} | {declared}/{total} | "
                f"{enforced}/{total} | {probes} |"
            )

    lines.extend(["", "## Duplicate classification", ""])
    lines.append("Same wording on different assets is reported as template reuse, not as a duplicate QA sample.")
    lines.extend(["", "| class | groups | tasks in groups | blocking |", "|---|---:|---:|---|"])
    duplicate_counts = Counter(group["classification"] for group in report["duplicate_groups"])
    for classification in ("duplicate_task_id", "duplicate_qa_sample", "template_reuse"):
        groups = [group for group in report["duplicate_groups"] if group["classification"] == classification]
        task_ids = {item["task_id"] for group in groups for item in group["items"]}
        lines.append(
            f"| `{classification}` | {duplicate_counts[classification]} | {len(task_ids)} | "
            f"{'yes' if any(group['blocking'] for group in groups) else 'no'} |"
        )
    template_groups = sorted(
        (group for group in report["duplicate_groups"] if group["classification"] == "template_reuse"),
        key=lambda group: group["count"],
        reverse=True,
    )
    if template_groups:
        lines.extend(["", "### Repeated templates", "", "| count | instruction |", "|---:|---|"])
        for group in template_groups[:20]:
            instruction = group["instruction"].replace("|", "\\|")
            lines.append(f"| {group['count']} | {instruction} |")

    actionable = [result for result in report["tasks"] if result["decision"] != "pass"]
    lines.extend(
        [
            "",
            "## Review queue",
            "",
            "| grade | decision | task | contract | findings | eval |",
            "|---|---|---|---|---|---|",
        ]
    )
    for result in actionable:
        contract = result["contract"]
        codes = ", ".join(sorted({finding["code"] for finding in result["findings"]}))
        observed = result.get("eval", {}).get("observed_baselines", [])
        contract_text = f"{contract['answer_field_count']} fields; asks {', '.join(contract['asked_output_concepts']) or '-'}"
        lines.append(
            f"| **{result['classification']['grade']}** | {result['decision']} | "
            f"`{result['task_id']}` | {contract_text} | "
            f"{codes} | {', '.join(observed) or '-'} |"
        )
    if not actionable:
        lines.append("| qualified | pass | - | - | No task-level review needed. | - |")

    if len(report["tasks"]) <= 50:
        lines.extend(["", "## Question contract inventory", ""])
        lines.extend(["| task | ? | requested outputs | answer fields | GT status |", "|---|---:|---|---|---|"])
        for result in report["tasks"]:
            contract = result["contract"]
            lines.append(
                f"| `{result['task_id']}` | {contract['question_mark_count']} | "
                f"{', '.join(contract['asked_output_concepts']) or '-'} | "
                f"{', '.join(contract['answer_fields']) or '-'} | `{contract['ground_truth_status']}` |"
            )
    return "\n".join(lines) + "\n"


def write_report(report: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
    with (out_dir / "review_queue.jsonl").open("w", encoding="utf-8") as handle:
        for result in report["tasks"]:
            if result["decision"] != "pass":
                handle.write(json.dumps(result, ensure_ascii=False) + "\n")
    with (out_dir / "task_classifications.jsonl").open("w", encoding="utf-8") as handle:
        for result in report["tasks"]:
            record = {
                "task_id": result["task_id"],
                "family": result["family"],
                "decision": result["decision"],
                **result["classification"],
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
