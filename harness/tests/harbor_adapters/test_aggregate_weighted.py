"""Tests for the cross-task weighted aggregation post-processor."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.harbor.aggregate_weighted import (  # noqa: E402
    aggregate,
    domain_v_class,
    load_results,
    parse_task_name,
    validate_official_aggregation,
)
from scripts.harbor.security_contract import SecurityContractError  # noqa: E402


# ── task name parsing ───────────────────────────────────────────────────── #
@pytest.mark.parametrize(
    "name,domain,vc",
    [
        ("sci3d/sv-biology-v1-0001", "biology", "V1"),
        ("sv-robotics-v5-0001", "robotics", "V5"),
        ("sci3d/sv-topological-photonics-v3-0001", "topological-photonics", "V3"),
        # K-axis (medical pilot) and Genesis physics naming.
        ("sci3d/sv-medical-k5-0008", "medical", "K5"),
        ("sci3d/genesis-p2-cut-dragon-fragments", "physics", "P2"),
        ("sci3d/genesis-p1-sand-wheel-cascade", "physics", "P1"),
        ("not-a-task", "", ""),
    ],
)
def test_parse_task_name(name, domain, vc):
    assert parse_task_name(name) == (domain, vc)


def test_domain_v_class_metadata_wins():
    # explicit metadata overrides whatever the name would parse to
    t = {
        "task_name": "sci3d/genesis-p2-foo",
        "metadata": {"domain": "physics", "v_class": "V6"},
    }
    assert domain_v_class(t) == ("physics", "V6")


def test_domain_v_class_falls_back_to_name():
    t = {"task_name": "sci3d/sv-medical-k3-0005", "metadata": {}}
    assert domain_v_class(t) == ("medical", "K3")


# ── aggregation modes ───────────────────────────────────────────────────── #
def _citable(rewards: dict) -> dict:
    """Trials carry the gate's verdict; ``aggregate`` drops uncertified ones.

    These tests are about the arithmetic, so every fixture is certified. The
    filter's own behaviour is tested in ``test_citability_filter.py``.
    """
    return {**rewards, "process_citable": 1.0}


def _trials():
    # biology: 1 trial @0.2 ; robotics: 3 trials @0.8,0.8,0.8
    return [
        {"task_name": "sci3d/sv-biology-v1-0001", "rewards": _citable({"reward": 0.2})},
        {"task_name": "sci3d/sv-robotics-v1-0001", "rewards": _citable({"reward": 0.8})},
        {"task_name": "sci3d/sv-robotics-v4-0001", "rewards": _citable({"reward": 0.8})},
        {"task_name": "sci3d/sv-robotics-v5-0001", "rewards": _citable({"reward": 0.8})},
    ]


def _runtime_attestation(*names: str) -> dict:
    return {
        "schema_version": "sciencevision.agent_boundary.v1",
        "status": "runtime-attested",
        "attestation_scope": "trusted-host-runtime",
        "issuer": "pytest-host-launcher",
        "egress_canary": {
            "status": "pass",
            "same_runtime": True,
            "agent_egress_blocked": True,
            "environment_egress_blocked": True,
            "verifier_egress_blocked": True,
            "agent_policy_enforced": True,
        },
        "tasks": [
            {"task_name": name, "task_toml_sha256": "a" * 64}
            for name in names
        ],
    }


def test_flat_mean():
    s = aggregate(_trials(), mode="flat")
    # (0.2 + 0.8*3) / 4 = 0.65
    assert s["keys"]["reward"] == pytest.approx(0.65)


def test_domain_equal_cancels_imbalance():
    s = aggregate(_trials(), mode="domain_equal")
    # mean(biology=0.2, robotics=0.8) = 0.5  (robotics no longer dominates)
    assert s["keys"]["reward"] == pytest.approx(0.5)


def test_weighted_domain_and_vclass():
    weights = {"domain": {"biology": 3.0, "robotics": 1.0}, "default": 1.0}
    s = aggregate(_trials(), mode="weighted", weights=weights)
    # weights: biology 3*0.2 ; robotics 1*0.8 each
    # num = 3*0.2 + 1*0.8*3 = 0.6 + 2.4 = 3.0 ; den = 3 + 3 = 6 ; = 0.5
    assert s["keys"]["reward"] == pytest.approx(0.5)


def test_breakdown_present():
    s = aggregate(_trials(), mode="domain_equal")
    bd = s["breakdown"]
    assert bd["by_domain"]["robotics"]["n"] == 3
    assert bd["by_domain"]["biology"]["mean"] == pytest.approx(0.2)
    assert bd["by_v_class"]["V1"]["n"] == 2  # biology-v1 + robotics-v1


def test_multiple_numeric_keys():
    trials = [
        {"task_name": "sv-biology-v1-0001",
         "rewards": _citable({"reward": 0.5, "llm_judge": 0.4})},
        {"task_name": "sv-robotics-v1-0001",
         "rewards": _citable({"reward": 0.9, "llm_judge": 1.0})},
    ]
    s = aggregate(trials, mode="flat")
    assert set(s["keys"]) >= {"reward", "llm_judge"}


def test_official_aggregate_requires_attestation_and_all_citable():
    trials = _trials()
    receipt = _runtime_attestation(
        "sv-biology-v1-0001",
        "sv-robotics-v1-0001",
        "sv-robotics-v4-0001",
        "sv-robotics-v5-0001",
    )
    report = validate_official_aggregation(trials, receipt)
    assert report["citability"]["n_citable"] == len(trials)

    ungated = [{**trials[0], "rewards": {"reward": 0.2}}]
    with pytest.raises(SecurityContractError, match="uncertified"):
        validate_official_aggregation(ungated, _runtime_attestation("sv-biology-v1-0001"))

    with pytest.raises(SecurityContractError, match="does not cover"):
        validate_official_aggregation(trials, _runtime_attestation("sv-biology-v1-0001"))


def test_explicitly_route_ineligible_trial_is_excluded_from_formal_aggregate():
    trials = [
        {
            "task_name": "sv-biology-v1-0001",
            "rewards": _citable({"reward": 0.8}),
            "metadata": {"evaluation_eligible": True},
        },
        {
            "task_name": "sv-medical-k3-0001",
            "rewards": _citable({"reward": 1.0}),
            "metadata": {
                "evaluation_eligible": False,
                "route_asset_visibility": {"status": "route_ineligible"},
            },
        },
    ]
    summary = aggregate(trials, mode="flat")
    assert summary["n_trials"] == 1
    assert summary["n_evaluation_ineligible_excluded"] == 1
    assert summary["keys"]["reward"] == pytest.approx(0.8)

    report = validate_official_aggregation(
        trials, _runtime_attestation("sv-biology-v1-0001"),
    )
    assert report["citability"]["n_citable"] == 1
    assert report["citability"]["n_evaluation_ineligible_excluded"] == 1


# ── loading ─────────────────────────────────────────────────────────────── #
def test_load_results_from_dir(tmp_path: Path):
    d1 = tmp_path / "t1"
    d1.mkdir()
    (d1 / "result.json").write_text(json.dumps({
        "task_name": "sci3d/sv-medical-v2-0001",
        "verifier_result": {"rewards": _citable({"reward": 0.7})},
    }))
    d2 = tmp_path / "t2"
    d2.mkdir()
    (d2 / "result.json").write_text(json.dumps({
        "task_name": "sci3d/sv-astronomy-v1-0001",
        "verifier_result": {"rewards": _citable({"reward": 0.3})},
    }))
    trials = load_results(tmp_path)
    assert len(trials) == 2
    s = aggregate(trials, mode="domain_equal")
    assert s["n_trials"] == 2
    assert s["keys"]["reward"] == pytest.approx(0.5)


def test_load_results_from_array(tmp_path: Path):
    f = tmp_path / "results.json"
    f.write_text(json.dumps([
        {"task_name": "sv-biology-v1-0001", "rewards": {"reward": 1.0}},
    ]))
    trials = load_results(f)
    assert len(trials) == 1 and trials[0]["rewards"]["reward"] == 1.0


def test_load_results_retains_unscored_row_for_citability_gate(tmp_path: Path):
    f = tmp_path / "results.json"
    f.write_text(json.dumps([
        {"task_name": "sv-biology-v1-0001", "rewards": _citable({"reward": 1.0})},
        {"task_name": "sv-biology-v1-0002", "error": "agent crashed"},
    ]))
    rows = load_results(f)
    assert len(rows) == 2
    with pytest.raises(SecurityContractError, match="uncertified"):
        validate_official_aggregation(
            rows,
            _runtime_attestation("sv-biology-v1-0001", "sv-biology-v1-0002"),
        )


def test_load_results_excludes_infra_invalid_scorer_audit(tmp_path: Path):
    trial = tmp_path / "t1"
    verifier = trial / "verifier"
    verifier.mkdir(parents=True)
    (trial / "result.json").write_text(json.dumps({
        "task_name": "sci3d/sv-medical-v2-0001",
        "verifier_result": {"rewards": _citable({"reward": 0.0})},
    }))
    (verifier / "sci3d_scores.json").write_text(json.dumps({
        "evaluation_eligible": False,
        "aggregate_excluded": True,
        "formal_reward_source": "infra_invalid_not_scorable",
    }))
    rows = load_results(tmp_path)
    assert rows[0]["aggregate_excluded"] is True
    summary = aggregate(rows, mode="flat", citable_only=False)
    assert summary["n_trials"] == 0
    assert summary["n_evaluation_ineligible_excluded"] == 1
