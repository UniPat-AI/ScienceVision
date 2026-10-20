"""Coverage for the capability-policy scan (T2).

``forbidden_tools`` in a sci3d task names a *library/API capability*
("nifti_centroid_api"), never an MCP tool name. The pre-existing tool-name check
therefore could never enforce it: the capability is realised inside a ``Bash``
command string or a ``Write``-authored script, and ``Bash``/``Write`` are on the
always-allowed generic-harness list. Net effect before this module: the policy
penalised the route that honestly named its tools, while the route that ran
``scipy.ndimage.center_of_mass`` over the staged ground-truth mask scored a
clean ``process_tool_policy = 1.0``.

The tests below pin the properties that keep this scan from becoming theatre:

  * a declared capability with no signature is reported as *not covered*, never
    as clean -- the same failure mode as the probe-alias no-op fixed in
    ``_qa_qc_shortcuts.attach_probe_results``
  * "cannot be checked" and "was checked and found cheating" stay separate
    fields, so the gate neither cries wolf nor claims a clean bill of health
  * generic primitives (``np.nonzero``) are suspects, not violations, until they
    co-occur with a staged ``/data/`` asset path in the same command
  * only *agent-authored* text is scanned (commands run, files written) -- never
    observations, or the agent would be penalised for what a tool handed back

Run:  python -m pytest tests/harbor_adapters/test_capability_policy.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from harbor_adapters.sci3d import _capability_policy as P  # noqa: E402
from harbor_adapters.sci3d import _capability_signatures as S  # noqa: E402
from harbor_adapters.sci3d import _tool_names as N  # noqa: E402


def _call(name: str, arguments_text: str, step: int = 1) -> dict:
    return {"step": step, "name": name, "arguments_text": arguments_text}


# -- 1. tool-name classification ------------------------------------------- #
def test_mcp_prefix_is_stripped_to_the_bare_tool_name():
    assert N.normalize_tool_name("mcp__sciencevision__sv3d_open_scene") == "sv3d_open_scene"
    assert N.normalize_tool_name("sv3d_open_scene") == "sv3d_open_scene"
    assert N.normalize_tool_name(None) == ""


def test_verify_translate_keeps_its_private_alias_for_backwards_compatibility():
    """tests/harbor_adapters/test_generic_harness_scoring.py imports this name."""
    from harbor_adapters.sci3d._verify_translate import _normalize_tool_name

    assert _normalize_tool_name("mcp__x__sv3d_render") == "sv3d_render"


@pytest.mark.parametrize("name", ["Bash", "bash", "Write", "Edit", "Task", "apply_patch"])
def test_code_authoring_tools_are_scanned(name):
    assert N.is_code_authoring_tool(name)


@pytest.mark.parametrize("name", ["Read", "Grep", "Glob", "TodoWrite", "sv3d_render", "finish"])
def test_non_authoring_tools_are_not_scanned(name):
    """A tool that cannot *execute* agent-authored code carries no capability.

    ``Write`` is scanned and ``Read`` is not for a concrete reason: an agent
    normally writes ``analyze.py`` then runs ``python analyze.py``, so the
    forbidden call lives in the Write arguments while the Bash command only
    names the script. Scanning ``Read`` instead would flag the *staging*
    defect, which is T1's, not this scan's.
    """
    assert not N.is_code_authoring_tool(name)


# -- 2. the signature table is honest about its own coverage --------------- #
# The 11 capabilities carrying ~95% of all forbidden_tools declarations in the
# shipped corpus. An unmapped name here is a silently unenforced policy.
_HIGH_VOLUME = [
    "pdb_contact_table", "nifti_centroid_api", "slice_index_api",
    "nibabel_header_lookup", "gemmi_spacegroup_lookup", "cif_read_symmetry",
    "clash_score_api", "plip_analyze", "smiles_to_conformer",
    "rdkit_calc_dihedral", "rdkit_assign_stereo",
]


@pytest.mark.parametrize("capability", _HIGH_VOLUME)
def test_every_high_volume_capability_has_at_least_one_signature(capability):
    assert capability in S.CAPABILITY_SIGNATURES
    assert S.CAPABILITY_SIGNATURES[capability], f"{capability} maps to an empty tuple"


@pytest.mark.parametrize("capability", _HIGH_VOLUME)
def test_every_high_volume_capability_has_a_violation_signature(capability):
    """A capability with only suspect-level signatures can never raise a
    violation on its own, so it would read as enforced while being unable to
    conclude anything."""
    severities = {sev for _, sev in S.CAPABILITY_SIGNATURES[capability]}
    assert S.VIOLATION in severities, f"{capability} has no violation-level signature"


def test_a_name_cannot_be_both_mapped_and_declared_unenforceable():
    overlap = set(S.CAPABILITY_SIGNATURES) & set(S.UNENFORCEABLE_BY_SIGNATURE)
    assert overlap == set()


def test_every_unenforceable_entry_states_why():
    for name, reason in S.UNENFORCEABLE_BY_SIGNATURE.items():
        assert reason.strip(), f"{name} is declared unenforceable with no reason"


def test_every_signature_pattern_compiles():
    for capability, sigs in S.CAPABILITY_SIGNATURES.items():
        for pattern, severity in sigs:
            assert severity in (S.VIOLATION, S.SUSPECT), (capability, pattern, severity)
            P.compile_pattern(pattern)  # must not raise


# -- 3. a declared-but-unmapped capability is NOT clean -------------------- #
def test_an_unmapped_capability_is_reported_as_uncovered_not_clean():
    audit = P.audit_capability_policy([], forbidden=["totally_new_api_v9"])
    assert audit["unmapped"] == ["totally_new_api_v9"]
    assert audit["coverage"] == 0.0
    assert audit["enforced"] == []
    # policy_ok stays True: nothing was *found*. The caller must read coverage
    # to learn that nothing could be looked for either.
    assert audit["policy_ok"] is True


def test_an_unenforceable_capability_is_named_with_its_reason():
    name = sorted(S.UNENFORCEABLE_BY_SIGNATURE)[0]
    audit = P.audit_capability_policy([], forbidden=[name])
    assert name in audit["unenforceable"]
    assert audit["unenforceable"][name] == S.UNENFORCEABLE_BY_SIGNATURE[name]
    assert name not in audit["unmapped"], "a known-unmappable name is not a TODO"
    assert audit["coverage"] == 0.0


def test_declaring_nothing_is_vacuously_fully_covered():
    audit = P.audit_capability_policy([_call("Bash", "ls /data")], forbidden=None)
    assert audit["declared"] == []
    assert audit["coverage"] == 1.0
    assert audit["policy_ok"] is True
    assert audit["violations"] == []


def test_coverage_is_the_fraction_of_declared_names_with_a_signature():
    audit = P.audit_capability_policy(
        [], forbidden=["nifti_centroid_api", "totally_new_api_v9"]
    )
    assert audit["coverage"] == 0.5
    assert audit["enforced"] == ["nifti_centroid_api"]


# -- 4. the actual T2 defect: a forbidden capability inside a Bash string -- #
_CENTROID_CMD = (
    "python -c \"import nibabel as nib, numpy as np; "
    "from scipy.ndimage import center_of_mass; "
    "m=nib.load('/data/mask.nii.gz').get_fdata(); print(center_of_mass(m))\""
)


def test_a_forbidden_capability_run_through_bash_is_a_violation():
    audit = P.audit_capability_policy(
        [_call("Bash", _CENTROID_CMD)], forbidden=["nifti_centroid_api"]
    )
    assert audit["policy_ok"] is False
    assert [f["capability"] for f in audit["violations"]] == ["nifti_centroid_api"]
    assert "center_of_mass" in audit["violations"][0]["match"]
    assert audit["violations"][0]["tool"] == "Bash"


def test_the_bash_tool_name_itself_stays_allowed():
    """The fix must not be "forbid Bash". compute_process_rewards is untouched:
    naming your tools honestly still costs nothing."""
    from harbor_adapters.sci3d._verify_translate import compute_process_rewards

    rewards = compute_process_rewards(
        ["Bash", "Read"], allowed=["sv3d_open_scene"], forbidden=["nifti_centroid_api"]
    )
    assert rewards["process_tool_policy"] == 1.0


def test_a_capability_realised_in_a_written_script_is_caught():
    calls = [
        _call(
            "Write",
            '{"file_path": "/tmp/a.py", "content": '
            '"from rdkit.Chem import AssignStereochemistry"}',
            step=1,
        ),
        _call("Bash", "python /tmp/a.py", step=2),
    ]
    audit = P.audit_capability_policy(calls, forbidden=["rdkit_assign_stereo"])
    assert audit["policy_ok"] is False
    assert audit["violations"][0]["tool"] == "Write"
    assert audit["violations"][0]["step"] == 1


def test_only_the_declared_capability_is_scanned_for():
    """A signature for a capability the task did not forbid must not fire."""
    audit = P.audit_capability_policy(
        [_call("Bash", _CENTROID_CMD)], forbidden=["plip_analyze"]
    )
    assert audit["violations"] == []
    assert audit["policy_ok"] is True


def test_observations_are_never_scanned():
    """Only agent-authored text counts. A tool that *returns* the string
    'center_of_mass' must not incriminate the agent."""
    calls = [{
        "step": 1, "name": "Bash", "arguments_text": "ls /data",
        "observation_text": _CENTROID_CMD, "outcome": "ok",
    }]
    audit = P.audit_capability_policy(calls, forbidden=["nifti_centroid_api"])
    assert audit["violations"] == []


# -- 5. two-tier severity and the staged-asset escalation ----------------- #
def test_a_generic_primitive_alone_is_a_suspect_not_a_violation():
    audit = P.audit_capability_policy(
        [_call("Bash", "python -c 'import numpy as np; print(np.nonzero([0,1]))'")],
        forbidden=["nifti_centroid_api"],
    )
    assert audit["violations"] == []
    assert [f["capability"] for f in audit["suspects"]] == ["nifti_centroid_api"]
    assert audit["policy_ok"] is True, "a suspect alone must not fail the policy"


def test_a_generic_primitive_over_a_staged_asset_escalates_to_a_violation():
    """This is the rule that catches "the agent ran numpy over the staged mask"
    without flagging every innocent use of numpy."""
    audit = P.audit_capability_policy(
        [_call("Bash", "python -c \"import numpy as np, nibabel as nib; "
                       "a=nib.load('/data/mask.nii.gz').get_fdata(); "
                       "print(np.nonzero(a))\"")],
        forbidden=["nifti_centroid_api"],
    )
    assert audit["policy_ok"] is False
    finding = audit["violations"][0]
    assert finding["escalated"] is True
    assert any("/data/mask.nii.gz" in p for p in finding["staged_paths"])


def test_escalation_needs_the_staged_path_in_the_same_command():
    """A staged path named in an *earlier* call does not retroactively escalate a
    later innocent numpy call -- co-occurrence is per-command by design, so the
    evidence in the audit stays a single self-contained excerpt."""
    calls = [
        _call("Bash", "ls -l /data/mask.nii.gz", step=1),
        _call("Bash", "python -c 'import numpy as np; print(np.nonzero([0,1]))'", step=2),
    ]
    audit = P.audit_capability_policy(calls, forbidden=["nifti_centroid_api"])
    assert audit["violations"] == []
    assert audit["suspects"], "still recorded as a suspect"


def test_a_relative_data_path_also_counts_as_staged():
    audit = P.audit_capability_policy(
        [_call("Bash", "python -c \"import numpy;numpy.nonzero(open('data/seg.nii.gz','rb'))\"")],
        forbidden=["nifti_centroid_api"],
    )
    assert audit["policy_ok"] is False


# -- 6. shape, caps, immutability ----------------------------------------- #
def test_findings_are_capped_and_the_drop_is_disclosed():
    calls = [_call("Bash", _CENTROID_CMD, step=i) for i in range(P.MAX_FINDINGS + 5)]
    audit = P.audit_capability_policy(calls, forbidden=["nifti_centroid_api"])
    assert len(audit["violations"]) == P.MAX_FINDINGS
    assert audit["violations_truncated"] is True


def test_evidence_excerpts_are_bounded():
    audit = P.audit_capability_policy(
        [_call("Bash", "pad " * 1200 + "center_of_mass(m) " + "tail " * 1200)],
        forbidden=["nifti_centroid_api"],
    )
    assert len(audit["violations"][0]["excerpt"]) <= P.MAX_EXCERPT_CHARS


def test_audit_does_not_mutate_its_inputs():
    calls = [_call("Bash", _CENTROID_CMD)]
    snapshot = [dict(c) for c in calls]
    forbidden = ["nifti_centroid_api"]
    P.audit_capability_policy(calls, forbidden=forbidden)
    assert calls == snapshot
    assert forbidden == ["nifti_centroid_api"]


def test_audit_carries_a_schema_version_and_the_scanned_call_count():
    audit = P.audit_capability_policy(
        [_call("Bash", "ls"), _call("Read", "x"), _call("Write", "y")],
        forbidden=["nifti_centroid_api"],
    )
    assert audit["schema_version"] == P.CAPABILITY_POLICY_SCHEMA_VERSION
    assert audit["scanned_calls"] == 2, "Read is not scanned"


def test_malformed_calls_are_skipped_rather_than_raising():
    audit = P.audit_capability_policy(
        [None, "nonsense", {}, {"name": "Bash"}, _call("Bash", _CENTROID_CMD)],
        forbidden=["nifti_centroid_api"],
    )
    assert audit["policy_ok"] is False


# -- 7. splitting the tool-name policy so P2's bias is not reproduced ----- #
def test_calling_a_forbidden_tool_name_is_separated_from_leaving_the_allowlist():
    split = P.split_tool_policy(
        ["mcp__sv__sv3d_render", "sv3d_get_state", "Bash", "plip_analyze"],
        allowed=["sv3d_render"],
        forbidden=["plip_analyze"],
    )
    assert split["forbidden_called"] == ["plip_analyze"]
    assert split["outside_allowlist"] == ["sv3d_get_state"]
    assert "Bash" not in split["outside_allowlist"], "generic harness tools stay exempt"
    assert "sv3d_render" not in split["outside_allowlist"]


def test_no_allowlist_means_nothing_is_outside_it():
    split = P.split_tool_policy(["sv3d_get_state"], allowed=None, forbidden=None)
    assert split["outside_allowlist"] == []
    assert split["forbidden_called"] == []
