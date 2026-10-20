"""report_ev2_threeway: per-task content/process marking + summary shape."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import scripts.harbor.report_ev2_threeway as mod  # noqa: E402


def _write_score(root: Path, baseline: str, task_id: str, content, process, reward):
    d = root / f"jobs-ev2-codex-{baseline}" / "2026-07-30__00-00-00" / f"{task_id}__hash" / "verifier"
    d.mkdir(parents=True, exist_ok=True)
    (d / "sci3d_scores.json").write_text(json.dumps({
        "task_id": task_id, "domain": "chemistry",
        "llm_judge": {"value": content, "raw": {"rationale": "because"}},
        "process_judge": {"value": process},
        "reward": reward, "tool_names": [],
    }))


def test_summary_has_per_task_content_and_process(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    _write_score(tmp_path, "cc", "sv-chem-conformation-01", 0.8, 0.6, 0.74)
    data = {b: mod.collect_baseline(b) for b in mod.BASELINES}
    summary = mod.build_summary(data)
    cell = summary["per_task"]["sv-chem-conformation-01"]["cc"]
    assert cell["content"] == 0.8
    assert cell["process"] == 0.6
    assert cell["reward"] == 0.74
    assert cell["rationale"] == "because"
    md = mod.render_md(summary)
    assert "### per-task content" in md
    assert "### per-task process" in md
