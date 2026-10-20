"""Coverage for per-call outcome extraction -- "did the MCP call succeed?".

Before this module the eval recorded *that* a tool was called and nothing about
whether it worked: ``_parse_atif_tool_names`` kept only ``function_name``, and
``_parse_atif_steps`` flattened every observation into one 800-char blob for the
judge prompt. So a ``ccmcp`` run in which every ``sv3d_render`` returned
``{"error": "..."}`` was indistinguishable from one in which they all succeeded
-- both scored ``process_rendered = 1.0``.

Repo convention (CLAUDE.md): MCP tool handlers return ``{"error": "..."}``
rather than raising across the boundary. That envelope is what makes outcomes
recoverable from the trajectory at all, so it is the primary signal here.

The properties pinned below are the ones that decide whether the resulting
number can be trusted:

  * ``{"error": null}`` and ``{"errors": []}`` are successes -- only a truthy
    ``error``/``isError`` key is a failure
  * "no MCP calls" yields ``attempted = 0`` and a success rate of ``None``, not
    a spurious perfect 1.0; a rate is meaningless without its qualifier
  * unknown-outcome calls are excluded from the rate's denominator and counted
    separately, so an unparseable observation cannot silently read as success

Run:  python -m pytest tests/harbor_adapters/test_tool_outcomes.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from harbor_adapters.sci3d import _tool_names as N  # noqa: E402
from harbor_adapters.sci3d import _tool_outcomes as O  # noqa: E402


# -- 1. classifying one observation payload -------------------------------- #
def test_the_repo_error_envelope_is_a_failure():
    outcome, text = O.classify_content(json.dumps({"error": "scene not open"}))
    assert outcome == O.OUTCOME_ERROR
    assert "scene not open" in text


def test_a_null_error_key_is_a_success():
    """``{"error": null}`` is how a handler says "nothing went wrong"."""
    assert O.classify_content(json.dumps({"error": None, "ok": True}))[0] == O.OUTCOME_OK


def test_an_empty_errors_list_is_a_success():
    """The plural key is a payload field, not the error envelope."""
    assert O.classify_content(json.dumps({"errors": [], "n": 3}))[0] == O.OUTCOME_OK


def test_the_mcp_is_error_flag_is_a_failure():
    assert O.classify_content(json.dumps({"isError": True, "content": "x"}))[0] == O.OUTCOME_ERROR
    assert O.classify_content(json.dumps({"is_error": True}))[0] == O.OUTCOME_ERROR


def test_a_false_is_error_flag_is_a_success():
    assert O.classify_content(json.dumps({"isError": False}))[0] == O.OUTCOME_OK


def test_a_plain_text_error_prefix_is_a_failure():
    for text in ("Error: no such file", "ERROR: boom", "Traceback (most recent call last):",
                 "tool_use_error: bad args"):
        assert O.classify_content(text)[0] == O.OUTCOME_ERROR, text


def test_renderer_skipped_is_a_failure_with_its_reason_preserved():
    """A GL-less host returns ``renderer_skipped`` by design (CLAUDE.md). The
    agent still got no image, so for "did the call succeed" this is a no -- but
    the reason is kept verbatim so a reviewer can separate it from a real bug."""
    outcome, text = O.classify_content(json.dumps({"renderer_skipped": "no EGL device"}))
    assert outcome == O.OUTCOME_ERROR
    assert "renderer_skipped" in text


def test_an_empty_observation_is_unknown_not_success():
    assert O.classify_content("")[0] == O.OUTCOME_UNKNOWN
    assert O.classify_content(None)[0] == O.OUTCOME_UNKNOWN
    assert O.classify_content("   ")[0] == O.OUTCOME_UNKNOWN


def test_ordinary_output_is_a_success():
    assert O.classify_content("wrote /logs/agent/view_00.png")[0] == O.OUTCOME_OK
    assert O.classify_content(json.dumps({"image_png_base64": "iVBOR"}))[0] == O.OUTCOME_OK


def test_the_word_error_inside_ordinary_output_is_not_a_failure():
    """Substring matching anywhere would make any log line mentioning errors a
    failure; only a prefix or the envelope key counts."""
    assert O.classify_content("0 errors, 3 warnings")[0] == O.OUTCOME_OK


# -- 2. MCP-vs-harness call classification --------------------------------- #
@pytest.mark.parametrize("name", [
    "mcp__sciencevision__sv3d_render", "sv3d_open_scene", "sv_render_nifti_overlay",
])
def test_sci3d_tools_count_as_mcp_calls_under_both_harnesses(name):
    """The Claude Code CLI calls these as ``mcp__sciencevision__*``; the sci3d
    agent calls the same tool bare. Both must land in the same bucket or the
    ``cc``-vs-``ccmcp`` ablation compares different denominators."""
    assert N.is_mcp_call(name)


@pytest.mark.parametrize("name", ["Bash", "Read", "Write", "finish", "TodoWrite"])
def test_harness_tools_are_not_mcp_calls(name):
    assert not N.is_mcp_call(name)


# -- 3. parsing an ATIF trajectory into calls with outcomes ---------------- #
def _traj(*steps: dict) -> dict:
    return {"steps": list(steps)}


def _step(step_id: int, calls: list[dict], results: list[dict]) -> dict:
    return {
        "step_id": step_id,
        "tool_calls": calls,
        "observation": {"results": results},
    }


def test_calls_are_paired_to_their_observation_by_call_id(tmp_path: Path):
    traj = _traj(_step(
        1,
        [{"tool_call_id": "a", "function_name": "sv3d_open_scene", "arguments": {"path": "/data/x"}},
         {"tool_call_id": "b", "function_name": "sv3d_render", "arguments": {"view": "front"}}],
        [{"source_call_id": "b", "content": json.dumps({"error": "no scene"})},
         {"source_call_id": "a", "content": json.dumps({"ok": True})}],
    ))
    path = tmp_path / "trajectory.json"
    path.write_text(json.dumps(traj))

    calls = O.parse_calls_from_path(path)
    by_name = {c["name"]: c for c in calls}
    assert by_name["sv3d_open_scene"]["outcome"] == O.OUTCOME_OK
    assert by_name["sv3d_render"]["outcome"] == O.OUTCOME_ERROR
    assert "no scene" in by_name["sv3d_render"]["error_text"]


def test_a_single_unlabelled_result_still_pairs_with_a_single_call(tmp_path: Path):
    """Not every harness fills ``source_call_id``. One call plus one result is
    unambiguous, and refusing to pair it would report every such run as
    ``unknown`` -- an artifact indistinguishable from a broken parser."""
    traj = _traj(_step(
        1,
        [{"tool_call_id": "a", "function_name": "sv3d_render", "arguments": {}}],
        [{"content": json.dumps({"error": "boom"})}],
    ))
    path = tmp_path / "t.json"
    path.write_text(json.dumps(traj))
    assert O.parse_calls_from_path(path)[0]["outcome"] == O.OUTCOME_ERROR


def test_an_unpairable_call_is_unknown_not_ok(tmp_path: Path):
    traj = _traj(_step(
        1,
        [{"tool_call_id": "a", "function_name": "sv3d_render", "arguments": {}},
         {"tool_call_id": "b", "function_name": "sv3d_open_scene", "arguments": {}}],
        [{"source_call_id": "zz", "content": "irrelevant"}],
    ))
    path = tmp_path / "t.json"
    path.write_text(json.dumps(traj))
    assert {c["outcome"] for c in O.parse_calls_from_path(path)} == {O.OUTCOME_UNKNOWN}


def test_arguments_are_preserved_as_text_for_the_capability_scan(tmp_path: Path):
    traj = _traj(_step(
        1,
        [{"tool_call_id": "a", "function_name": "Bash",
          "arguments": {"command": "python -c 'from scipy.ndimage import center_of_mass'"}}],
        [{"source_call_id": "a", "content": "ok"}],
    ))
    path = tmp_path / "t.json"
    path.write_text(json.dumps(traj))
    call = O.parse_calls_from_path(path)[0]
    assert "center_of_mass" in call["arguments_text"]
    assert call["step"] == 1


def test_string_arguments_survive_as_text(tmp_path: Path):
    traj = _traj(_step(
        1, [{"tool_call_id": "a", "function_name": "Bash", "arguments": "ls /data"}],
        [{"source_call_id": "a", "content": "ok"}],
    ))
    path = tmp_path / "t.json"
    path.write_text(json.dumps(traj))
    assert O.parse_calls_from_path(path)[0]["arguments_text"] == "ls /data"


def test_a_missing_or_malformed_trajectory_yields_no_calls(tmp_path: Path):
    assert O.parse_calls_from_path(tmp_path / "absent.json") == ()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert O.parse_calls_from_path(bad) == ()


def test_codex_session_jsonl_recovers_mcp_calls_and_renderer_skips(tmp_path: Path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    lines = [
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "mcp__sciencevision__sv3d_render",
                "call_id": "render-1",
                "arguments": '{"session_id":"abc"}',
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "render-1",
                "output": json.dumps({"renderer_skipped": "no EGL device"}),
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "Bash",
                "call_id": "shell-1",
                "arguments": {"command": "ls /data"},
            },
        },
        {
            "type": "response_item",
            "payload": {"type": "function_call_output", "call_id": "shell-1", "output": "ok"},
        },
    ]
    (sessions / "rollout.jsonl").write_text("\n".join(json.dumps(line) for line in lines) + "\n")

    calls = O.parse_codex_session_calls(tmp_path)
    assert len(calls) == 2
    render = next(call for call in calls if call["name"].endswith("sv3d_render"))
    assert render["outcome"] == O.OUTCOME_ERROR
    assert "renderer_skipped" in str(render["error_text"])
    shell = next(call for call in calls if call["name"] == "Bash")
    assert shell["outcome"] == O.OUTCOME_OK
    assert shell["arguments_text"] == '{"command": "ls /data"}'


def test_merge_call_records_uses_shared_call_id_to_avoid_double_counting():
    atif = ({"name": "mcp__sciencevision__sv3d_render", "call_id": "same", "outcome": "ok"},)
    codex = ({"name": "mcp__sciencevision__sv3d_render", "call_id": "same", "outcome": "ok"},)
    merged = O.merge_call_records(atif, codex)
    assert len(merged) == 1


def test_every_call_is_parsed_not_only_the_judge_prompt_window(tmp_path: Path):
    """``_parse_atif_steps`` truncates to 24 steps for the judge prompt. The
    policy scan must see the whole run: a shortcut on step 40 is still a
    shortcut."""
    steps = [
        _step(i, [{"tool_call_id": f"c{i}", "function_name": "Bash", "arguments": {"command": "ls"}}],
              [{"source_call_id": f"c{i}", "content": "ok"}])
        for i in range(40)
    ]
    path = tmp_path / "t.json"
    path.write_text(json.dumps(_traj(*steps)))
    assert len(O.parse_calls_from_path(path)) == 40


# -- 4. aggregating to the run-level MCP signal ---------------------------- #
def _c(name: str, outcome: str) -> dict:
    return {"step": 1, "name": name, "arguments_text": "", "outcome": outcome,
            "error_text": None if outcome != O.OUTCOME_ERROR else "boom"}


def test_no_mcp_calls_reports_attempted_zero_and_no_rate():
    """A rate of 1.0 on zero attempts is the reading that would let a ``cc`` run
    (no MCP at all) look like flawless MCP usage."""
    summary = O.summarize_mcp([_c("Bash", O.OUTCOME_OK)])
    assert summary["calls"] == 0
    assert summary["success_rate"] is None


def test_the_success_rate_is_over_decided_calls_only():
    summary = O.summarize_mcp([
        _c("sv3d_render", O.OUTCOME_OK),
        _c("sv3d_render", O.OUTCOME_ERROR),
        _c("sv3d_open_scene", O.OUTCOME_UNKNOWN),
    ])
    assert summary["calls"] == 3
    assert summary["ok"] == 1
    assert summary["error"] == 1
    assert summary["unknown"] == 1
    assert summary["success_rate"] == 0.5


def test_all_unknown_outcomes_yield_no_rate_rather_than_zero():
    summary = O.summarize_mcp([_c("sv3d_render", O.OUTCOME_UNKNOWN)])
    assert summary["calls"] == 1
    assert summary["success_rate"] is None, "0.0 would read as 'every call failed'"


def test_per_tool_breakdown_names_the_tool_that_kept_failing():
    summary = O.summarize_mcp([
        _c("sv3d_render", O.OUTCOME_ERROR),
        _c("sv3d_render", O.OUTCOME_ERROR),
        _c("sv3d_open_scene", O.OUTCOME_OK),
    ])
    assert summary["by_tool"]["sv3d_render"] == {"ok": 0, "error": 2, "unknown": 0}
    assert summary["by_tool"]["sv3d_open_scene"] == {"ok": 1, "error": 0, "unknown": 0}


def test_error_samples_are_capped_and_the_drop_is_disclosed():
    calls = [_c("sv3d_render", O.OUTCOME_ERROR) for _ in range(O.MAX_ERROR_SAMPLES + 3)]
    summary = O.summarize_mcp(calls)
    assert len(summary["errors"]) == O.MAX_ERROR_SAMPLES
    assert summary["errors_truncated"] is True


def test_summarize_does_not_mutate_its_input():
    calls = [_c("sv3d_render", O.OUTCOME_OK)]
    snapshot = [dict(c) for c in calls]
    O.summarize_mcp(calls)
    assert calls == snapshot


def test_malformed_call_records_are_skipped_rather_than_raising():
    summary = O.summarize_mcp([None, "x", {}, _c("sv3d_render", O.OUTCOME_OK)])
    assert summary["calls"] == 1
    assert summary["success_rate"] == 1.0
