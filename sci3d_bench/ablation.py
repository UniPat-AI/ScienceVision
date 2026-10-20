"""Ablation deltas: Δ_image / Δ_visual / Δ_active per V-class."""
from __future__ import annotations
from dataclasses import dataclass
from collections import defaultdict
from statistics import mean


@dataclass(frozen=True)
class AblationCell:
    v_class: str
    n: int
    n_compliant: int
    mode1: float
    mode2: float
    mode3: float
    delta_image: float
    delta_visual: float
    delta_active: float
    mode1_compliant: float
    mode2_compliant: float
    mode3_compliant: float


def compute_deltas(rows: list) -> dict:
    """`rows` is a list of dicts with keys: task_id, v_class, mode, score, violation."""
    by_class = defaultdict(list)
    for row in rows:
        by_class[row["v_class"]].append(row)
    table = {}
    for v_class, class_rows in by_class.items():
        by_task = defaultdict(dict)
        violator_tasks = set()
        for r in class_rows:
            by_task[r["task_id"]][r["mode"]] = r["score"]
            if r.get("violation"):
                violator_tasks.add(r["task_id"])
        all_tasks = list(by_task.keys())
        compliant_tasks = [t for t in all_tasks if t not in violator_tasks]

        def _mode_mean(task_subset, mode):
            vals = [by_task[t][mode] for t in task_subset if mode in by_task[t]]
            return mean(vals) if vals else float("nan")

        m1, m2, m3 = (_mode_mean(all_tasks, m) for m in ("mode1_no_vision", "mode2_one_image", "mode3_free_visual"))
        c1, c2, c3 = (_mode_mean(compliant_tasks, m) for m in ("mode1_no_vision", "mode2_one_image", "mode3_free_visual"))
        table[v_class] = AblationCell(
            v_class=v_class,
            n=len(all_tasks),
            n_compliant=len(compliant_tasks),
            mode1=m1, mode2=m2, mode3=m3,
            delta_image=m2 - m1, delta_visual=m3 - m1, delta_active=m3 - m2,
            mode1_compliant=c1, mode2_compliant=c2, mode3_compliant=c3,
        )
    return table
