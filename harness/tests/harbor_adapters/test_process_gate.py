"""Coverage for the process gate ``score_rewards`` now emits.

Two questions the eval could not previously answer are answered here, off data
already on disk:

  * did the agent use a forbidden *capability*? -- previously unchecked, because
    ``forbidden_tools`` names library capabilities (``nifti_centroid_api``) that
    never appear as tool names, and ``Bash`` was exempted wholesale. The ``cc``
    route scored 0.932 on ``med_ct_geometry`` with ``process_rendered = 0.00``
    and a clean ``process_tool_policy = 1.0``.
  * did the MCP calls succeed? -- previously unrecorded; only the tool *name*
    was kept, so an all-errors ``ccmcp`` run looked identical to a clean one.

Three invariants make the gate safe to act on, and each has a test:

  1. The formal reward does not move. ``reward == answer_score``; every new key
     is ``process_``-prefixed and diagnostic. Weight-0 process signals are a
     deliberate design choice, not an oversight to be corrected here.
  2. Citability blocks only on positive evidence -- a capability violation or a
     genuinely forbidden tool. Leaving a narrow allow-list must NOT block: the
     allow-list enumerates sci3d tools only, so gating on it would re-create the
     bias against routes that honestly name their tools, which is the defect
     this change exists to remove.
  3. "Checked nothing" never reports as "clean". A declared capability with no
     signature lowers ``process_capability_coverage`` and is named in the audit,
     so a reader cannot mistake an unenforceable policy for a satisfied one.

Run:  python -m pytest tests/harbor_adapters/test_process_gate.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_CENTROID_CMD = (
    "python -c \"import nibabel as nib; from scipy.ndimage import center_of_mass; "
    "m=nib.load('/data/mask.nii.gz').get_fdata(); print(center_of_mass(m))\""
)

_META = {
    "domain": "medical",
    "answer_schema": {"kind": "categorical", "fields": ["laterality"]},
    "ground_truth": {"value": {"laterality": "left"}},
    "scorer": {"primary": "exact_match"},
}


def _score(tmp_path: Path, steps: list[dict], **meta_over):
    """Write a trajectory and score it, returning ``(rewards, audit)``."""
    (tmp_path / "trajectory.json").write_text(json.dumps({"steps": steps}))
    from harbor_adapters.sci3d._score_core import score_rewards

    return score_rewards(
        {**_META, **meta_over}, task_id="t1", instruction="x", agent_dir=tmp_path
    )


def _step(step_id: int, name: str, arguments: dict | str, content: str = "ok") -> dict:
    return {
        "step_id": step_id,
        "tool_calls": [
            {"tool_call_id": f"c{step_id}", "function_name": name, "arguments": arguments}
        ],
        "observation": {"results": [{"source_call_id": f"c{step_id}", "content": content}]},
    }


# -- 1. the defect: a forbidden capability run through Bash ---------------- #
def test_a_forbidden_capability_in_a_bash_command_is_now_caught(tmp_path: Path):
    """The exact ``cc`` shortcut: compute the centroid off the staged mask."""
    rewards, audit = _score(
        tmp_path,
        [_step(1, "Bash", {"command": _CENTROID_CMD})],
        forbidden_tools=["nifti_centroid_api"],
    )
    assert rewards["process_capability_policy"] == 0.0
    assert audit["capability_policy"]["policy_ok"] is False
    assert audit["capability_policy"]["violations"][0]["capability"] == "nifti_centroid_api"


def test_the_legacy_tool_name_policy_still_reads_clean_on_that_run(tmp_path: Path):
    """Pinning *why* the new key is needed rather than a tweak to the old one:
    the tool-name check sees only ``Bash``, which is exempt, so it cannot ever
    notice this. The two signals must be reported separately."""
    rewards, _ = _score(
        tmp_path,
        [_step(1, "Bash", {"command": _CENTROID_CMD})],
        forbidden_tools=["nifti_centroid_api"],
    )
    assert rewards["process_tool_policy"] == 1.0
    assert rewards["process_capability_policy"] == 0.0


def test_an_innocent_bash_run_stays_clean(tmp_path: Path):
    rewards, audit = _score(
        tmp_path,
        [_step(1, "Bash", {"command": "ls -la /data && head -c 100 /data/notes.txt"})],
        forbidden_tools=["nifti_centroid_api"],
    )
    assert rewards["process_capability_policy"] == 1.0
    assert audit["capability_policy"]["violations"] == []


def test_a_capability_written_into_a_script_is_caught_at_the_write(tmp_path: Path):
    """The ordinary agent pattern: write ``analyze.py``, then run it. Scanning
    only the command would see ``python analyze.py`` and nothing else."""
    rewards, _ = _score(
        tmp_path,
        [
            _step(1, "Write", {"file_path": "/tmp/a.py", "content": _CENTROID_CMD}),
            _step(2, "Bash", {"command": "python /tmp/a.py"}),
        ],
        forbidden_tools=["nifti_centroid_api"],
    )
    assert rewards["process_capability_policy"] == 0.0


def test_a_task_declaring_nothing_forbidden_is_vacuously_clean(tmp_path: Path):
    rewards, audit = _score(tmp_path, [_step(1, "Bash", {"command": _CENTROID_CMD})])
    assert rewards["process_capability_policy"] == 1.0
    assert audit["capability_policy"]["declared"] == []
    assert rewards["process_capability_coverage"] == 1.0


# -- 2. coverage is disclosed, never a finding ----------------------------- #
def test_an_unenforceable_capability_lowers_coverage_without_failing(tmp_path: Path):
    """``metadata_lookup_answer`` describes reading a staged value -- a plain
    file read with no fingerprint. Reporting it as a violation would flag nearly
    every task and the gate would be ignored; reporting it as clean would be the
    checker-that-checks-nothing bug. So: coverage drops, policy holds."""
    rewards, audit = _score(
        tmp_path,
        [_step(1, "Bash", {"command": "cat /data/meta.json"})],
        forbidden_tools=["metadata_lookup_answer", "nifti_centroid_api"],
    )
    assert rewards["process_capability_policy"] == 1.0
    assert rewards["process_capability_coverage"] == 0.5
    assert "metadata_lookup_answer" in audit["capability_policy"]["unenforceable"]


def test_coverage_and_policy_are_both_needed_to_claim_clean(tmp_path: Path):
    """A run that declares only unenforceable capabilities passes the policy
    while nothing was actually checked. The audit must say so."""
    rewards, audit = _score(
        tmp_path,
        [_step(1, "Bash", {"command": "cat /data/meta.json"})],
        forbidden_tools=["metadata_lookup_answer"],
    )
    assert rewards["process_capability_policy"] == 1.0
    assert rewards["process_capability_coverage"] == 0.0
    assert audit["process_gate"]["notes"], "an unchecked policy must be disclosed"


# -- 3. citability: what blocks and what does not -------------------------- #
def test_a_capability_violation_blocks_citability(tmp_path: Path):
    _, audit = _score(
        tmp_path,
        [_step(1, "Bash", {"command": _CENTROID_CMD})],
        forbidden_tools=["nifti_centroid_api"],
    )
    assert audit["process_gate"]["citable"] is False
    assert "capability_violation" in audit["process_gate"]["blockers"]


def test_calling_a_genuinely_forbidden_tool_blocks_citability(tmp_path: Path):
    _, audit = _score(
        tmp_path,
        [_step(1, "sv3d_get_state", {})],
        forbidden_tools=["sv3d_get_state"],
    )
    assert audit["process_gate"]["citable"] is False
    assert "forbidden_tool_called" in audit["process_gate"]["blockers"]


def test_leaving_a_narrow_allowlist_does_not_block_citability(tmp_path: Path):
    """``allowed_tools`` enumerates sci3d tools only. A route that calls an
    unlisted sci3d tool is an allow-list-authoring fact, not a breach -- gating
    on it would penalise exactly the routes that name their tools honestly,
    which is the bias this change removes."""
    _, audit = _score(
        tmp_path,
        [_step(1, "sv3d_render", {"view": "front"})],
        allowed_tools=["sv3d_open_scene"],
    )
    gate = audit["process_gate"]
    assert gate["citable"] is True
    assert gate["blockers"] == []
    assert "sv3d_render" in audit["tool_policy"]["outside_allowlist"]


def test_a_clean_run_is_citable(tmp_path: Path):
    _, audit = _score(
        tmp_path,
        [_step(1, "sv3d_open_scene", {"path": "/data/a.glb"})],
        forbidden_tools=["trimesh"],
        allowed_tools=["sv3d_open_scene"],
    )
    assert audit["process_gate"]["citable"] is True
    assert audit["process_gate"]["blockers"] == []


# -- 4. the MCP-health signal (the user's Q2) ------------------------------ #
def test_mcp_errors_are_surfaced_instead_of_counting_as_usage(tmp_path: Path):
    """The whole point: ``process_rendered`` is 1.0 because the name appeared,
    while every call actually failed. Both numbers must be visible."""
    rewards, audit = _score(
        tmp_path,
        [
            _step(1, "sv3d_open_scene", {"path": "/data/a.glb"},
                  content=json.dumps({"error": "no such asset"})),
            _step(2, "sv3d_render", {"view": "front"},
                  content=json.dumps({"error": "scene not open"})),
        ],
    )
    assert rewards["process_rendered"] == 1.0, "name-based signal is unchanged"
    assert rewards["process_mcp_attempted"] == 1.0
    assert rewards["process_mcp_success_rate"] == 0.0
    assert audit["mcp"]["error"] == 2
    assert audit["mcp"]["by_tool"]["sv3d_render"]["error"] == 1


def test_a_route_with_no_mcp_calls_is_not_credited_with_a_perfect_rate(tmp_path: Path):
    """The ``cc`` route uses no MCP by construction. A 1.0 success rate there
    would be read off the leaderboard as flawless MCP usage."""
    rewards, audit = _score(tmp_path, [_step(1, "Bash", {"command": "ls /data"})])
    assert rewards["process_mcp_attempted"] == 0.0
    assert rewards["process_mcp_success_rate"] == 0.0
    assert audit["mcp"]["calls"] == 0
    assert audit["mcp"]["success_rate"] is None, "None, not 0.0, in the audit"


def test_a_healthy_mcp_run_reports_a_full_rate(tmp_path: Path):
    rewards, _ = _score(
        tmp_path,
        [
            _step(1, "sv3d_open_scene", {"path": "/data/a.glb"}, content='{"ok": true}'),
            _step(2, "sv3d_render", {"view": "front"}, content='{"image_png_base64": "x"}'),
        ],
    )
    assert rewards["process_mcp_attempted"] == 1.0
    assert rewards["process_mcp_success_rate"] == 1.0


def test_the_rate_is_zero_qualified_by_attempted_not_by_itself(tmp_path: Path):
    """``process_mcp_success_rate`` is 0.0 both for "never tried" and for "tried
    and every call failed". They are distinguished by ``process_mcp_attempted``,
    so any consumer of the rate must read both keys."""
    none_tried, _ = _score(tmp_path, [_step(1, "Bash", {"command": "ls"})])
    all_failed, _ = _score(
        tmp_path,
        [_step(1, "sv3d_render", {}, content=json.dumps({"error": "boom"}))],
    )
    assert none_tried["process_mcp_success_rate"] == all_failed["process_mcp_success_rate"]
    assert none_tried["process_mcp_attempted"] != all_failed["process_mcp_attempted"]


# -- 5. the formal reward must not move ----------------------------------- #
def test_the_formal_reward_still_equals_the_answer_score(tmp_path: Path):
    rewards, audit = _score(
        tmp_path,
        [_step(1, "Bash", {"command": _CENTROID_CMD})],
        forbidden_tools=["nifti_centroid_api"],
    )
    assert rewards["reward"] == rewards["answer_score"]
    assert audit["weights"] == {"answer": 1.0, "process": 0.0}
    assert audit["process_rewards_diagnostic_only"] is True


@pytest.mark.parametrize("key", [
    "process_capability_policy", "process_capability_coverage",
    "process_mcp_attempted", "process_mcp_success_rate",
])
def test_every_new_reward_key_is_process_prefixed_and_in_unit_range(tmp_path: Path, key):
    """The ``process_`` prefix is load-bearing beyond convention:
    ``scripts/_qa_qc_shortcuts.py::content_reward`` takes the max of every
    numeric reward *except* ``reward`` and ``process*`` keys, so an unprefixed
    diagnostic would silently inflate the shortcut detector's content signal."""
    rewards, _ = _score(
        tmp_path,
        [_step(1, "Bash", {"command": _CENTROID_CMD})],
        forbidden_tools=["nifti_centroid_api"],
    )
    assert key in rewards
    assert key.startswith("process_")
    assert 0.0 <= rewards[key] <= 1.0


def test_all_reward_values_stay_floats(tmp_path: Path):
    """Harbor's reward channel is numeric; a ``None`` success rate leaking into
    ``rewards`` would break serialisation. ``None`` lives in the audit only."""
    rewards, _ = _score(tmp_path, [_step(1, "Bash", {"command": "ls"})])
    assert all(isinstance(v, float) for v in rewards.values())


def test_scoring_does_not_mutate_the_metadata_it_is_given(tmp_path: Path):
    (tmp_path / "trajectory.json").write_text(
        json.dumps({"steps": [_step(1, "Bash", {"command": _CENTROID_CMD})]})
    )
    from harbor_adapters.sci3d._score_core import score_rewards

    meta = {**_META, "forbidden_tools": ["nifti_centroid_api"]}
    snapshot = json.loads(json.dumps(meta))
    score_rewards(meta, task_id="t1", instruction="x", agent_dir=tmp_path)
    assert meta == snapshot


def test_a_missing_trajectory_degrades_without_raising(tmp_path: Path):
    from harbor_adapters.sci3d._score_core import score_rewards

    rewards, audit = score_rewards(
        {**_META, "forbidden_tools": ["nifti_centroid_api"]},
        task_id="t1", instruction="x", agent_dir=tmp_path,
    )
    assert rewards["process_capability_policy"] == 1.0
    assert rewards["process_mcp_attempted"] == 0.0
    assert audit["capability_policy"]["scanned_calls"] == 0
