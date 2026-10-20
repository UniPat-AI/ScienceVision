"""The dev subset's selection rule, pinned on synthetic rows.

The committed subset (`scripts/eval/dev_subset.json`) is derived from a bundle
that lives outside this repository, so it cannot be regenerated in CI. The *rule*
can be, and that is what these tests are for: the file is only trustworthy if the
thing that produced it is.

The distinctions that matter, and that a test has to actually distinguish:

- the quota is per sub-domain and the set is deliberately not proportional, so a
  3-row sub-domain and a 50-row sub-domain both spend the same two slots;
- the top-up only runs when the clean pool could not fill the quota, and when it
  runs it spends the slot on a viewer family the sub-domain does not hold --
  an unexercised family is a family a regression cannot appear in;
- "outside the clean pool" is not the same as "warned": a row can be excluded for
  not being `evaluation_ready` and carry no `warn_kinds` at all, which is the
  shape that makes `arch_structural_deformation` unreachable, so the reason has to
  survive into the emitted file;
- a sub-domain the bundle cannot fill is recorded as `under_quota`, not silently
  dropped, because a hole in the spine is the one failure the subset cannot
  tolerate.

Run:  python -m pytest tests/scripts/test_build_dev_subset.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.eval.build_dev_subset import (  # noqa: E402
    PER_SUB_DOMAIN,
    build,
    has_answer,
    is_clean,
    is_free_text,
    not_clean_reasons,
    select,
)


def _row(task_id, sub_domain, viewer, *, ready=True, warns=(), n=1, domain="d",
         answer_form="structured"):
    return {
        "task_id": task_id,
        "folder": f"tasks/{domain}/{sub_domain}/001-{task_id}",
        "sub_domain": sub_domain,
        "domain": domain,
        "viewer": viewer,
        "task_type": "t",
        "asset_count": n,
        "asset_2d": None,
        "mcp_allow_list_source": "declared",
        "evaluation_ready": ready,
        "answer_form": answer_form,
        "warn_kinds": list(warns),
    }


def _write_bundle(tmp_path, rows, gt_by_id=None):
    """A bundle in miniature: the index plus one task.json per row.

    `build` reaches past the index to read each task's ground truth, so a
    fixture that only writes index.jsonl no longer reproduces the real layout.
    """
    gt_by_id = gt_by_id or {}
    (tmp_path / "index.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    for row in rows:
        task_dir = tmp_path / row["folder"]
        task_dir.mkdir(parents=True, exist_ok=True)
        task = {"ground_truth": gt_by_id.get(row["task_id"], {"value": "x"})}
        (task_dir / "task.json").write_text(json.dumps(task), encoding="utf-8")


def test_clean_means_ready_and_unwarned():
    assert is_clean(_row("x", "sd", "F"))
    assert not is_clean(_row("x", "sd", "F", warns=["k"]))
    assert not is_clean(_row("x", "sd", "F", ready=False))
    assert not is_clean(_row("x", "sd", "F", ready=False, warns=["k"]))


def test_a_not_ready_row_is_not_described_as_warned():
    """The label a reader sees has to match why the row is out of the pool."""
    assert not_clean_reasons(_row("x", "sd", "F", ready=False)) == [
        "not evaluation_ready"
    ]
    assert not_clean_reasons(_row("x", "sd", "F", warns=["k"])) == ["k"]
    assert not_clean_reasons(_row("x", "sd", "F", ready=False, warns=["k"])) == [
        "k",
        "not evaluation_ready",
    ]
    assert not_clean_reasons(_row("x", "sd", "F")) == []


def test_core_admits_only_clean_rows():
    rows = [_row("c", "sd", "F"), _row("w", "sd", "F", warns=["k"])]
    assert all(is_clean(r) for r in select(rows)["core"])


def test_the_quota_is_two_per_sub_domain():
    rows = [_row(f"a{i}", "sd", "F", n=i) for i in range(5)]
    result = select(rows)
    assert len(result["core"]) == PER_SUB_DOMAIN
    assert result["achieved"]["n"] == PER_SUB_DOMAIN


def test_the_quota_is_spent_per_sub_domain_not_on_the_largest():
    """The spine: a 1-row sub-domain and a 5-row sub-domain get the same two."""
    rows = [_row(f"big{i}", "sd_big", "F") for i in range(5)]
    rows += [_row("small0", "sd_small", "F")]
    per = {
        r["sub_domain"]
        for r in select(rows)["core"]
    }
    assert per == {"sd_big", "sd_small"}


def test_cheapest_wins_and_the_id_breaks_the_tie():
    rows = [_row("zz", "sd", "F", n=1), _row("aa", "sd", "F", n=1), _row("xx", "sd", "F", n=3)]
    assert [r["task_id"] for r in select(rows)["core"]] == ["aa", "zz"]


def test_fewest_warnings_outranks_cost():
    """A warned cheap row must not displace an unwarned expensive one."""
    rows = [
        _row("cheap-warned", "sd", "F", warns=["k"], n=1),
        _row("dear-clean", "sd", "F", n=9),
    ]
    assert [r["task_id"] for r in select(rows)["core"]] == ["dear-clean"]


def test_no_top_up_when_the_clean_pool_fills_the_quota():
    rows = [_row("c1", "sd", "F", n=1), _row("c2", "sd", "F", n=1)]
    rows += [_row("w", "sd", "F", warns=["k"], n=1)]
    result = select(rows)
    assert result["coverage"] == []
    assert result["achieved"]["untrustworthy"] == 0


def test_top_up_prefers_a_viewer_family_the_sub_domain_does_not_hold():
    """The F2 slot costs more than the F1 alternative and is taken anyway.

    This is the whole point of the preference: spending the last slot on a second
    row of a family already exercised buys a repeat, and a family absent from the
    set is a family a regression cannot show up in.
    """
    rows = [
        _row("clean-f1", "sd", "F1", n=1),
        _row("dear-f2", "sd", "F2", ready=False, n=5),
        _row("cheap-f1", "sd", "F1", ready=False, n=1),
    ]
    result = select(rows)
    assert [r["task_id"] for r in result["coverage"]] == ["dear-f2"]
    assert "F2" in result["achieved"]["viewer_families"]


def test_top_up_falls_back_to_the_pick_key_when_every_family_is_held():
    rows = [
        _row("clean-f1", "sd", "F1", n=1),
        _row("dear-f1", "sd", "F1", ready=False, n=5),
        _row("cheap-f1", "sd", "F1", ready=False, n=1),
    ]
    assert [r["task_id"] for r in select(rows)["coverage"]] == ["cheap-f1"]


def test_an_unfillable_quota_is_recorded_rather_than_dropped():
    """`cad_geometry_drawings` holds one row in the whole bundle. The shortfall is
    a property of the corpus, so the file has to carry it, not hide it."""
    rows = [_row("only", "sd_thin", "F"), _row("a", "sd_ok", "F"), _row("b", "sd_ok", "F")]
    result = select(rows)
    assert result["gaps"]["under_quota"] == {"sd_thin": 1}
    assert result["gaps"]["sub_domains"] == []
    assert result["achieved"]["quota_met"] == ["sd_ok"]


def test_a_sub_domain_absent_from_the_pick_is_reported_as_a_gap():
    rows = [_row("a", "sd_ok", "F")]
    assert select(rows)["gaps"]["sub_domains"] == []


def test_every_row_is_emitted_once():
    rows = [_row(f"a{i}", "sd_a", "F") for i in range(3)]
    rows += [_row("b", "sd_b", "F", warns=["k"])]
    result = select(rows)
    ids = [r["task_id"] for r in result["core"] + result["coverage"]]
    assert len(ids) == len(set(ids))


def test_no_sub_domain_exceeds_the_quota():
    rows = [_row(f"a{i}", "sd", "F", n=i) for i in range(6)]
    result = select(rows)
    assert len(result["core"] + result["coverage"]) == PER_SUB_DOMAIN


def test_the_tier_is_provenance_and_the_flag_is_trust():
    """`core` is clean by construction; `coverage` is usually not, and the trust
    signal a consumer filters on is the per-row flag rather than the tier."""
    rows = [_row("a", "sd_a", "F"), _row("b", "sd_b", "F", warns=["k"])]
    result = select(rows)
    assert all(is_clean(r) for r in result["core"])
    assert all(not is_clean(r) for r in result["coverage"])


def test_build_records_the_provenance_that_determines_the_pick(tmp_path):
    """The subset is only comparable across runs if the file says what produced
    it -- the index hash and the row counts are the part that can move."""
    rows = [_row(f"a{i}", "sd_a", "F") for i in range(3)]
    _write_bundle(tmp_path, rows)
    doc = build(tmp_path)
    assert doc["schema_version"] == "sciencevision.eval_dev_subset.v1"
    assert doc["bundle"]["rows"] == len(rows)
    assert len(doc["bundle"]["index_sha256"]) == 64
    assert doc["per_sub_domain"] == PER_SUB_DOMAIN
    assert doc["achieved"]["n"] == len(doc["core"]) + len(doc["coverage"])
    assert doc["achieved"]["trustworthy"] == len(doc["core"])


def test_every_emitted_row_carries_its_reason_for_being_outside_the_pool(tmp_path):
    rows = [
        _row("clean", "sd", "F"),
        _row("unready", "sd", "F", ready=False),
        _row("warned", "sd", "F", warns=["k"]),
    ]
    _write_bundle(tmp_path, rows)
    doc = build(tmp_path)
    by_id = {r["task_id"]: r for r in doc["core"] + doc["coverage"]}
    assert by_id["clean"]["not_clean_reasons"] == []
    assert by_id["clean"]["score_is_trustworthy"] is True
    for r in doc["core"] + doc["coverage"]:
        assert r["score_is_trustworthy"] == (r["not_clean_reasons"] == [])


def test_the_tsv_carries_every_row_and_leads_with_the_task_id():
    """The flat file is for browsing and diffing, so it has to hold the same set
    the JSON does -- a TSV that silently dropped a row would be worse than none."""
    from scripts.eval.build_dev_subset import TSV_COLUMNS, tsv_text

    rows = [_row(f"a{i}", "sd_a", "F") for i in range(3)]
    rows += [_row("b", "sd_b", "F", warns=["k"])]
    result = select(rows, text_fields={"b": ["why"]})
    doc = {
        tier: [dict(r, score_is_trustworthy=is_clean(r)) for r in result[tier]]
        for tier in ("core", "coverage", "text_path")
    }

    lines = tsv_text(doc).splitlines()
    assert lines[0].split("\t") == list(TSV_COLUMNS)
    assert TSV_COLUMNS[0] == "task_id"
    body = [line.split("\t") for line in lines[1:]]
    assert len(body) == sum(len(doc[t]) for t in doc)
    for cells in body:
        assert len(cells) == len(TSV_COLUMNS)


def test_the_tsv_marks_the_tier_each_row_came_from():
    from scripts.eval.build_dev_subset import tsv_text

    doc = {
        "core": [dict(_row("c", "sd", "F"), score_is_trustworthy=True)],
        "coverage": [dict(_row("v", "sd", "F"), score_is_trustworthy=False)],
        "text_path": [dict(_row("t", "sd", "F"), score_is_trustworthy=True)],
    }
    tiers = {
        line.split("\t")[0]: line.split("\t")[1]
        for line in tsv_text(doc).splitlines()[1:]
    }
    assert tiers == {"c": "core", "v": "coverage", "t": "text_path"}


def test_self_check_catches_a_core_row_that_is_not_clean(tmp_path):
    from scripts.eval.build_dev_subset import _self_check

    rows = [_row("a", "sd", "F")]
    _write_bundle(tmp_path, rows)
    doc = build(tmp_path)
    doc["core"][0]["score_is_trustworthy"] = False
    doc["core"][0]["warn_kinds"] = ["k"]
    assert any("non-clean" in p for p in _self_check(doc))


def test_self_check_catches_an_absolute_folder(tmp_path):
    from scripts.eval.build_dev_subset import _self_check

    rows = [_row("a", "sd", "F")]
    _write_bundle(tmp_path, rows)
    doc = build(tmp_path)
    doc["core"][0]["folder"] = "/abs/" + doc["core"][0]["folder"]
    assert any("absolute path" in p for p in _self_check(doc))


def test_a_sentence_is_free_text_and_a_label_is_not():
    assert is_free_text("The supports at both ends are unchanged and the "
                        "deformation is symmetric about the midspan.")
    for label in ("none", "midspan", "clashing", "x-axis", "non-planar", 138, True):
        assert not is_free_text(label), label


def test_free_text_is_measured_in_characters_because_cjk_has_no_spaces():
    """The bug this pins: a whitespace word count reads a Chinese sentence as a
    single word, so every CJK prose field looked like a one-word label and the
    text-path tier missed three questions."""
    assert is_free_text("设备机组顶部与上方最近横梁之间，最小净距为138mm")
    assert is_free_text("缺陷位于腹梁长度方向，距一端约9.000m，位于梁中部区域")
    # Short CJK labels stay labels.
    assert not is_free_text("上层")
    assert not is_free_text("腹梁")


def test_the_text_path_tier_ignores_the_quota():
    """A sub-domain that already spent both slots still contributes a row when a
    field is prose -- the tier exists to exercise the comparator, and letting
    breadth veto it would drop exactly the questions it was added for."""
    rows = [_row(f"a{i}", "sd_a", "F", n=i) for i in range(4)]
    result = select(rows, text_fields={"a3": ["why"]})
    assert [r["task_id"] for r in result["core"]] == ["a0", "a1"]
    assert [r["task_id"] for r in result["text_path"]] == ["a3"]
    assert len(result["core"] + result["coverage"] + result["text_path"]) == 3


def test_a_text_path_row_is_not_one_already_picked():
    rows = [_row("a1", "sd_a", "F", n=1), _row("a2", "sd_a", "F", n=2)]
    result = select(rows, text_fields={"a1": ["why"], "a2": ["why"]})
    ids = [r["task_id"] for r in result["core"] + result["text_path"]]
    assert len(ids) == len(set(ids))
    assert result["text_path"] == []


def test_the_text_path_tier_is_empty_without_free_text_fields():
    rows = [_row("a1", "sd_a", "F")]
    assert select(rows)["text_path"] == []


def test_a_sub_domain_is_not_reported_as_quota_met_because_of_a_text_row():
    """`quota_met` counts the quota tiers, not every emitted row.

    `sd_a` fills its one-row quota and then gains a text-path row on top. Counted
    over all emitted rows it looks like two rows against a quota of one, and the
    sub-domain drops off `quota_met` despite having filled the quota exactly.
    """
    rows = [
        _row("a1", "sd_a", "F", n=1),
        _row("a2", "sd_a", "F", warns=["k"], n=2),
    ]
    result = select(rows, per_sub_domain=1, text_fields={"a2": ["why"]})
    assert [r["task_id"] for r in result["core"]] == ["a1"]
    assert [r["task_id"] for r in result["text_path"]] == ["a2"]
    assert result["achieved"]["quota_met"] == ["sd_a"]
    assert result["gaps"]["under_quota"] == {}


def test_has_answer_is_exactly_the_absent_flag():
    assert has_answer(_row("x", "sd", "F"))
    assert has_answer(_row("x", "sd", "F", warns=["k"], ready=False))
    assert not has_answer(_row("x", "sd", "F", answer_form="absent"))


def test_a_row_with_no_answer_is_dropped_and_the_quota_refills():
    """The slot goes to the next question in that sub-domain, not to nobody.

    Carrying the answerless row would have spent a quota slot on a question that
    can only ever return nothing, and left the sub-domain looking thinner than
    its scorable pool actually is.
    """
    rows = [
        _row("a1", "sd_a", "F", n=1),
        _row("a2", "sd_a", "F", n=2),
        _row("a3", "sd_a", "F", n=3),
    ]
    with_absent = rows + [
        _row("a-absent", "sd_a", "F", ready=False, answer_form="absent", n=9)
    ]
    # `select` is handed the scorable pool; `build` is what applies the filter.
    assert [r["task_id"] for r in select(rows)["core"]] == ["a1", "a2"]
    assert "a-absent" not in {r["task_id"] for r in select(with_absent)["core"]}


def test_build_drops_answerless_rows_before_filling_the_quota(tmp_path):
    rows = [
        _row("a1", "sd_a", "F", n=1),
        _row("gone", "sd_a", "F", ready=False, answer_form="absent"),
        _row("a2", "sd_a", "F", n=2),
    ]
    _write_bundle(tmp_path, rows)
    doc = build(tmp_path)
    picked = [r["task_id"] for r in doc["core"] + doc["coverage"]]
    assert picked == ["a1", "a2"]
    assert doc["bundle"]["rows"] == 3
    assert doc["bundle"]["scorable_pool"] == 2
    assert [r["task_id"] for r in doc["pool_excluded"]] == ["gone"]
    assert doc["pool_excluded"][0]["reason"].startswith("no answer")


def test_self_check_catches_a_core_row_with_no_answer(tmp_path):
    from scripts.eval.build_dev_subset import _self_check

    rows = [_row("a", "sd", "F")]
    _write_bundle(tmp_path, rows)
    doc = build(tmp_path)
    doc["core"][0]["answer_form"] = "absent"
    assert any("no answer" in p for p in _self_check(doc))


def test_self_check_catches_a_sub_domain_over_the_quota(tmp_path):
    from scripts.eval.build_dev_subset import _self_check

    rows = [_row(f"a{i}", "sd", "F", n=i) for i in range(4)]
    _write_bundle(tmp_path, rows)
    doc = build(tmp_path)
    doc["core"].append(dict(doc["core"][0], task_id="extra"))
    assert any("exceeds the quota" in p for p in _self_check(doc))
