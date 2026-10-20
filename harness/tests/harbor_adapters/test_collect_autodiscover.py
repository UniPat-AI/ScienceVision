"""Auto-discovery of `<set>/*_final_tasks.jsonl` sets in `_index_sets`.

`collect_qagen_results._index_sets` historically only indexed the hardcoded
`_SETS` mapping, so any new (e.g. end2end-driver-produced) task set fell
through to `set == "_unknown"` and its per-trial runs were skipped by
`write_runs`. This test locks in that any `<set>/<anything>_final_tasks.jsonl`
under the tasks root is discovered automatically, without requiring a
matching `_SETS` entry.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.harbor.collect_qagen_results import _index_sets  # noqa: E402


def test_autodiscovers_final_tasks_jsonl(tmp_path):
    d = tmp_path / "MyEndToEndSet"
    d.mkdir()
    (d / "MyEndToEndSet_final_tasks.jsonl").write_text(
        json.dumps({"task_id": "abc", "domain": "medical"}) + "\n"
    )
    idx = _index_sets(tmp_path)
    assert idx["abc"]["set"] == "MyEndToEndSet"
    assert idx["abc"]["record"]["domain"] == "medical"


def test_hardcoded_sets_win_on_collision(tmp_path, monkeypatch):
    # An auto-discovered set sharing a task_id with a hardcoded _SETS entry
    # must be overwritten by the hardcoded entry (back-compat guarantee).
    import scripts.harbor.collect_qagen_results as mod

    monkeypatch.setattr(
        mod, "_SETS", {"HardcodedSet": "HardcodedSet_final_tasks.jsonl"}
    )

    discovered_dir = tmp_path / "DiscoveredSet"
    discovered_dir.mkdir()
    (discovered_dir / "DiscoveredSet_final_tasks.jsonl").write_text(
        json.dumps({"task_id": "shared", "domain": "discovered"}) + "\n"
    )

    hardcoded_dir = tmp_path / "HardcodedSet"
    hardcoded_dir.mkdir()
    (hardcoded_dir / "HardcodedSet_final_tasks.jsonl").write_text(
        json.dumps({"task_id": "shared", "domain": "hardcoded"}) + "\n"
    )

    idx = mod._index_sets(tmp_path)
    assert idx["shared"]["set"] == "HardcodedSet"
    assert idx["shared"]["record"]["domain"] == "hardcoded"
