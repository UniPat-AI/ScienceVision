"""Tests for the trajectory leak scanner (``scripts/harbor/_leak_probe.py``).

The scanner answers one question per trial: *did the agent obtain information
from outside its staged inputs?* It is the producer for ``leak_scan.json``,
which until now had no producer anywhere in the repo — the 2026-08-14 EV2
report only ever *consumed* the file, so the single artifact that says whether a
run was contaminated was unreproducible.

Schema pinned against ``harbor_adapters/sci3d/_translate.py:66-84``:
``step["tool_calls"][i] = {tool_call_id, function_name, arguments{}}`` and
``step["observation"]["results"][i]["content"]``.

Output schema is pinned to the existing consumer
(``ScienceVision-eval-artifacts/v21-ev2-report-20260814/build_report.py:121-123``),
which reads ``fetch_attempt`` / ``local_asset_file`` / ``fetch_url`` / ``steps``
per task and ``curl_or_wget`` / ``external_fetch`` / ``local_asset_file`` / ``n``
per summary cell. Those key names must not drift.

Run:  python -m pytest tests/harbor_adapters/test_leak_scan.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from scripts.harbor._leak_probe import (  # noqa: E402
    LEAK_SCAN_SCHEMA_VERSION,
    scan_trajectory,
    scan_trial,
    summarize,
)
from scripts.harbor.leak_scan import main as leak_scan_main  # noqa: E402
from scripts.harbor.leak_scan import parse_run_spec, scan_job  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────── #
def _step(tool: str, args: dict, observation: str = "", n: int = 1) -> dict:
    return {
        "step_id": n,
        "source": "agent",
        "message": "",
        "tool_calls": [
            {"tool_call_id": f"c{n}", "function_name": tool, "arguments": args}
        ],
        "observation": {"results": [{"source_call_id": f"c{n}", "content": observation}]},
    }


def _traj(*steps: dict) -> dict:
    return {"steps": list(steps)}


# ── 1. the clean case ────────────────────────────────────────────────────── #
def test_clean_local_only_trajectory_reports_no_leak():
    """cc/ccmcp read the staged file under /data. That is not a leak."""
    traj = _traj(
        _step("Bash", {"command": "python -c 'import nibabel; print(1)'"}, "1"),
        _step("Bash", {"command": "ls /data/"}, "lesion_mask.nii.gz  ct.nii.gz", n=2),
    )
    got = scan_trajectory(traj)
    assert got["curl"] is False
    assert got["fetch_attempt"] is False
    assert got["local_asset_file"] is False
    assert got["fetch_url"] is None
    assert got["steps"] == 2


def test_staged_asset_under_data_is_never_a_local_asset_file():
    """The whole point of /data is that it is staged. Route-independent rule."""
    traj = _traj(_step("Bash", {"command": "python parse.py /data/qm9_110589.xyz"}))
    assert scan_trajectory(traj)["local_asset_file"] is False


# ── 2. fetcher invocation (summary key: curl_or_wget) ────────────────────── #
def test_curl_invocation_is_detected():
    traj = _traj(_step("Bash", {"command": "curl -sL https://files.rcsb.org/download/1ABC.cif -o s.cif"}))
    got = scan_trajectory(traj)
    assert got["curl"] is True
    assert got["fetch_attempt"] is True
    assert got["fetch_url"] == "files.rcsb.org"


def test_wget_and_aria2c_and_git_clone_all_count_as_fetchers():
    for cmd in (
        "wget https://files.rcsb.org/download/1ABC.cif",
        "aria2c -x4 https://www.crystallography.net/cod/1000000.cif",
        "git clone https://github.com/x/y",
    ):
        assert scan_trajectory(_traj(_step("Bash", {"command": cmd})))["curl"] is True, cmd


def test_python_urllib_fetch_has_no_curl_but_is_an_external_fetch():
    """A urllib download is the obvious way around a curl-only detector."""
    traj = _traj(
        _step(
            "Bash",
            {"command": "python -c \"import urllib.request as u; u.urlretrieve('https://files.rcsb.org/download/1ABC.cif','s.cif')\""},
        )
    )
    got = scan_trajectory(traj)
    assert got["curl"] is True, "urllib.request is a fetcher, even without curl on the line"
    assert got["fetch_attempt"] is True


def test_bare_curl_version_is_a_fetcher_call_but_not_an_external_fetch():
    """`curl --version` reaches no host. Keep the two signals independent."""
    got = scan_trajectory(_traj(_step("Bash", {"command": "curl --version"})))
    assert got["curl"] is True
    assert got["fetch_attempt"] is False
    assert got["fetch_url"] is None


def test_webfetch_and_websearch_tool_calls_are_fetchers():
    """These are in _GENERIC_HARNESS_TOOLS, so tool_policy never flags them."""
    got = scan_trajectory(_traj(_step("WebFetch", {"url": "https://www.rcsb.org/structure/1ABC"})))
    assert got["curl"] is True
    assert got["fetch_attempt"] is True
    assert got["fetch_url"] == "www.rcsb.org"
    assert scan_trajectory(_traj(_step("WebSearch", {"query": "1ABC structure"})))["curl"] is True


# ── 3. localhost is not external ─────────────────────────────────────────── #
def test_localhost_sam_service_is_not_an_external_fetch():
    """ccmcp legitimately calls SAM_SERVICE_URL at localhost:8900."""
    for host in ("http://localhost:8900/segment", "http://127.0.0.1:8900/segment"):
        got = scan_trajectory(_traj(_step("Bash", {"command": f"curl {host}"})))
        assert got["fetch_attempt"] is False, host
        assert got["fetch_url"] is None, host


# ── 4. the fetch succeeded (local_asset_file) ────────────────────────────── #
def test_asset_extension_outside_data_is_a_local_asset_file():
    """A downloaded structure lands in cwd, not /data. That is a successful hack."""
    traj = _traj(
        _step("Bash", {"command": "curl -sL https://files.rcsb.org/download/1ABC.cif -o ./1abc.cif"}),
        _step("Bash", {"command": "ls -la"}, "1abc.cif  view_00.png", n=2),
    )
    got = scan_trajectory(traj)
    assert got["local_asset_file"] is True


def test_local_asset_file_detected_from_observation_text_alone():
    """The agent may download inside a script; only the output names the file."""
    traj = _traj(
        _step("Bash", {"command": "bash get.sh"}, "saved to /app/downloaded_1abc.mmcif")
    )
    assert scan_trajectory(traj)["local_asset_file"] is True


def test_rendered_views_are_not_asset_files():
    """cc2d's own staged PNGs must never read as a leak."""
    traj = _traj(_step("Bash", {"command": "ls /data/"}, "view_00.png view_01.png"))
    assert scan_trajectory(traj)["local_asset_file"] is False


# ── 5. evidence is retained, bounded, and auditable ──────────────────────── #
def test_evidence_records_why_each_signal_fired():
    traj = _traj(_step("Bash", {"command": "curl -sL https://files.rcsb.org/download/1ABC.cif -o a.cif"}))
    ev = scan_trajectory(traj)["evidence"]
    assert ev["fetcher"], "must say which fetcher matched"
    assert "files.rcsb.org" in ev["hosts"]
    assert any("a.cif" in s for s in ev["asset_files"])


def test_evidence_is_bounded_so_a_long_run_cannot_blow_up_the_report():
    traj = _traj(
        *[
            _step("Bash", {"command": f"curl https://h{i}.example.com/x{i}.cif -o f{i}.cif"}, n=i)
            for i in range(200)
        ]
    )
    ev = scan_trajectory(traj)["evidence"]
    assert len(ev["hosts"]) <= 10
    assert len(ev["asset_files"]) <= 10


# ── 6. malformed input fails soft, never silently clean ──────────────────── #
def test_missing_trajectory_file_is_reported_as_unscannable_not_as_clean():
    """A zero row and an unreadable row must not look the same. That blindness
    is exactly how the MOLECULAR_FORMATS gate reported 'checked and clean'."""
    got = scan_trial(Path("/nonexistent/trial"))
    assert got["scanned"] is False
    assert got["curl"] is None
    assert got["fetch_attempt"] is None
    assert got["local_asset_file"] is None


def test_a_scanned_clean_trial_is_marked_scanned():
    got = scan_trajectory(_traj(_step("Bash", {"command": "ls"})))
    assert got["scanned"] is True


def test_non_dict_arguments_do_not_crash_the_scanner():
    step = {
        "step_id": 1,
        "tool_calls": [{"function_name": "Bash", "arguments": "curl https://x.example.com/a.cif"}],
        "observation": None,
    }
    got = scan_trajectory({"steps": [step]})
    assert got["curl"] is True


# ── 7. scan_trial reads the real on-disk layout ──────────────────────────── #
def test_scan_trial_reads_agent_trajectory_json(tmp_path: Path):
    """Layout pinned to collect_qagen_results.collect_one: <trial>/agent/."""
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "trajectory.json").write_text(
        json.dumps(_traj(_step("Bash", {"command": "curl https://files.rcsb.org/x.cif -o x.cif"})))
    )
    got = scan_trial(tmp_path)
    assert got["scanned"] is True
    assert got["curl"] is True
    assert got["local_asset_file"] is True


def test_scan_trial_prefers_sci3d_trajectory_when_present(tmp_path: Path):
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "sci3d_trajectory.json").write_text(json.dumps(_traj(_step("finish", {}))))
    (agent / "trajectory.json").write_text(
        json.dumps(_traj(_step("Bash", {"command": "curl https://x.example.com/a.cif"})))
    )
    assert scan_trial(tmp_path)["curl"] is False


# ── 8. summary shape is pinned to the existing consumer ──────────────────── #
def test_summary_uses_the_key_names_build_report_reads():
    per_task = {
        "cc2d-3": {
            "sv-biol-a": {"curl": True, "fetch_attempt": True, "local_asset_file": True,
                          "fetch_url": "files.rcsb.org", "steps": 3, "scanned": True},
            "sv-biol-b": {"curl": False, "fetch_attempt": False, "local_asset_file": False,
                          "fetch_url": None, "steps": 2, "scanned": True},
        }
    }
    domains = {"sv-biol-a": "biology", "sv-biol-b": "biology"}
    cell = summarize(per_task, domains)["cc2d-3"]["biology"]
    assert cell["curl_or_wget"] == 1
    assert cell["external_fetch"] == 1
    assert cell["local_asset_file"] == 1
    assert cell["n"] == 2


def test_summary_counts_unscanned_separately_from_clean():
    per_task = {
        "cc": {
            "t1": {"curl": None, "fetch_attempt": None, "local_asset_file": None,
                   "fetch_url": None, "steps": 0, "scanned": False},
        }
    }
    cell = summarize(per_task, {"t1": "medical"})["cc"]["medical"]
    assert cell["n"] == 1
    assert cell["unscanned"] == 1
    assert cell["curl_or_wget"] == 0, "an unscanned trial is not evidence of cleanliness"


def test_summary_groups_unknown_domain_rather_than_dropping_the_task():
    per_task = {"cc": {"t1": {"curl": True, "fetch_attempt": True, "local_asset_file": False,
                              "fetch_url": "x.example.com", "steps": 1, "scanned": True}}}
    out = summarize(per_task, {})
    assert out["cc"]["unknown"]["n"] == 1
    assert out["cc"]["unknown"]["curl_or_wget"] == 1


def test_schema_version_is_declared():
    assert LEAK_SCAN_SCHEMA_VERSION.startswith("sciencevision.leak_scan.")


# ── 9. the run-walking driver ────────────────────────────────────────────── #
def test_parse_run_spec_accepts_two_and_three_field_forms():
    assert parse_run_spec("cc:/tmp/job-cc") == ("cc", Path("/tmp/job-cc"))
    # Same spec shape collect_qagen_results takes: the model field is dropped.
    assert parse_run_spec("cc2d-6:claude-opus-5:/tmp/j") == ("cc2d-6", Path("/tmp/j"))


def test_parse_run_spec_keeps_a_job_dir_containing_a_colon():
    route, job = parse_run_spec("cc:/tmp/run:2026/job")
    assert (route, job) == ("cc", Path("/tmp/run:2026/job"))


def test_parse_run_spec_rejects_a_spec_with_no_job_dir():
    for spec in ("cc", "cc:", ":/tmp/job"):
        with pytest.raises(ValueError):
            parse_run_spec(spec)


def _trial(job: Path, task_id: str, command: str) -> None:
    trial = job / task_id
    (trial / "agent").mkdir(parents=True)
    (trial / "agent" / "trajectory.json").write_text(
        json.dumps(_traj(_step("Bash", {"command": command})))
    )
    (trial / "result.json").write_text(json.dumps({"task_name": f"tasks/{task_id}"}))


def test_scan_job_keys_rows_by_task_id_from_result_json(tmp_path: Path):
    _trial(tmp_path, "sv-biol-0001", "curl https://files.rcsb.org/x.cif -o x.cif")
    _trial(tmp_path, "sv-chem-0002", "ls /data/")
    rows = scan_job(tmp_path)
    assert set(rows) == {"sv-biol-0001", "sv-chem-0002"}
    assert rows["sv-biol-0001"]["local_asset_file"] is True
    assert rows["sv-chem-0002"]["local_asset_file"] is False
    assert "trial_dir" in rows["sv-biol-0001"]


def test_scan_job_keeps_the_worst_repeat_trial(tmp_path: Path):
    """One leak contaminates the task; clean retries must not dilute it."""
    for i, cmd in enumerate(("ls /data/", "curl https://x.example.com/a.cif -o a.cif")):
        trial = tmp_path / f"trial-{i}"
        (trial / "agent").mkdir(parents=True)
        (trial / "agent" / "trajectory.json").write_text(
            json.dumps(_traj(_step("Bash", {"command": cmd})))
        )
        (trial / "result.json").write_text(json.dumps({"task_name": "tasks/sv-biol-0001"}))
    rows = scan_job(tmp_path)
    assert list(rows) == ["sv-biol-0001"]
    assert rows["sv-biol-0001"]["local_asset_file"] is True


def test_scan_job_on_a_missing_dir_returns_no_rows_rather_than_raising(tmp_path: Path):
    assert scan_job(tmp_path / "nope") == {}


# ── 10. end-to-end: the artifact a reviewer actually reads ───────────────── #
def test_main_writes_a_leak_scan_json_the_ev2_report_can_read(tmp_path, monkeypatch):
    """Pins the two halves of the on-disk contract at once: the per-task keys
    build_report.py reads, and the summary cell names."""
    clean, leaky = tmp_path / "job-cc", tmp_path / "job-cc2d-3"
    _trial(clean, "sv-biol-0001", "python -c 'import nibabel'")
    _trial(leaky, "sv-biol-0002", "curl -sL https://files.rcsb.org/download/1ABC.cif -o 1abc.cif")
    out = tmp_path / "reports" / "leak_scan.json"

    monkeypatch.setattr(
        sys, "argv",
        ["leak_scan", "--run", f"cc:{clean}", "--run", f"cc2d-3:{leaky}",
         "--tasks-root", str(tmp_path / "no-such-tasks-root"), "--out", str(out)],
    )
    leak_scan_main()

    payload = json.loads(out.read_text())
    assert payload["schema_version"] == LEAK_SCAN_SCHEMA_VERSION
    assert [r["route"] for r in payload["runs"]] == ["cc", "cc2d-3"]

    cc = payload["per_task"]["cc"]["sv-biol-0001"]
    assert set(cc) >= {"curl", "fetch_attempt", "fetch_url", "local_asset_file", "steps"}
    assert cc["local_asset_file"] is False

    hacked = payload["per_task"]["cc2d-3"]["sv-biol-0002"]
    assert hacked["fetch_url"] == "files.rcsb.org"
    assert hacked["local_asset_file"] is True

    # No tasks-root -> tasks land under 'unknown', never dropped.
    assert payload["summary"]["cc2d-3"]["unknown"]["local_asset_file"] == 1
    assert payload["summary"]["cc"]["unknown"]["local_asset_file"] == 0


def test_main_refuses_a_missing_job_dir_instead_of_reporting_it_clean(tmp_path, monkeypatch):
    """A silently-skipped job renders as a spotless route. Fail loudly."""
    out = tmp_path / "leak_scan.json"
    monkeypatch.setattr(
        sys, "argv",
        ["leak_scan", "--run", f"cc:{tmp_path / 'nope'}", "--out", str(out)],
    )
    with pytest.raises(SystemExit):
        leak_scan_main()
    assert not out.exists()
