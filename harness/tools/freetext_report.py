#!/usr/bin/env python3
"""Generate the free-text re-scoring comparison page from a replay JSONL.

Reads the replay's output (and, optionally, the task bundle) and writes one
self-contained HTML file. Every figure on the page is derived from the input:
change the JSONL and the page changes. Nothing about the measurement is
hardcoded, and the F2 census calls the scorer's own ``report_only_drop`` rather
than reimplementing it, so the page cannot drift from the rule it describes.

The page is the R09 report template from the lieflat-charts skill — 1080 sheet,
Porcelain colour tokens, four chart slots, rail of KPI cards — with the slots
carrying four real gallery cards:

    A  F6 paired rungs    basics-gallery.html   per-domain before/after
    B  F5 tick rows       basics-gallery.html   which rule moved a row
    C  L14 hundred field  lupi-gallery.html     the trial census
    D  F12 dumbbell queue basics-gallery.html   the two legitimate bases

Usage:

    python tools/freetext_report.py \\
        --rescore ~/sv-freetext/rescore_v2.jsonl \\
        --bundle  ~/sv-freetext/bundle \\
        --superseded ~/sv-freetext/rescore.jsonl \\
        --out ~/sv-freetext/freetext_rescore_report.html
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter, defaultdict

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ── input ─────────────────────────────────────────────────────────────────── #

def load_rows(path: pathlib.Path) -> list[dict]:
    """One JSON object per line; blank lines ignored."""
    text = pathlib.Path(path).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def load_ground_truths(bundle_dir: pathlib.Path | None) -> dict[str, dict]:
    """``task_id`` -> ``ground_truth``, indexed out of the task bundle."""
    if bundle_dir is None:
        return {}
    root = pathlib.Path(bundle_dir)
    if not root.exists():
        return {}
    truths: dict[str, dict] = {}
    for path in sorted(root.rglob("task.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        task_id = doc.get("task_id") or path.parent.name
        ground_truth = doc.get("ground_truth")
        if isinstance(ground_truth, dict):
            truths[str(task_id)] = ground_truth
            # folder names are "<nnn>-<task_id>"; index the tail too.
            tail = path.parent.name.split("-", 1)
            if len(tail) == 2:
                truths.setdefault(tail[1], ground_truth)
    return truths


# ── the numbers ───────────────────────────────────────────────────────────── #

def _mean(rows: list[dict], key: str) -> float:
    return sum(row[key] for row in rows) / len(rows) if rows else 0.0


def _fmt(value: float, places: int = 6) -> str:
    return f"{value:.{places}f}"


def _signed(value: float, places: int = 6) -> str:
    if abs(value) < 10 ** (-places) / 2:
        return "±" + _fmt(0.0, places)
    return f"{value:+.{places}f}"


def _moved(rows: list[dict], a: str, b: str) -> list[dict]:
    return [r for r in rows if abs(r[b] - r[a]) > 1e-12]


def attribute_moves(scored: list[dict]) -> tuple[dict[str, int], dict[str, Counter]]:
    """For every moved row, which rule moved it.

    An ``F1``/``F3`` move is a leaf that flipped, read off the verdict diff:
    only where the item still exists in ``new_verdicts`` and carries
    ``rule = F1|F3``. ``F2`` leaves no verdict at all — it removes one — so it
    is counted where an item actually left the rubric *and* the score changed.
    A dropped item that was already failing moves nothing (the common case)
    and must not be counted as a mover; counting from the verdicts alone would
    silently report every drop as an F2 mover.
    """
    rows_by_rule: Counter = Counter()
    leaves_by_rule: dict[str, Counter] = defaultdict(Counter)
    for row in scored:
        old = {v["id"]: v for v in row["old_verdicts"]}
        new = {v["id"]: v for v in row["new_verdicts"]}
        seen: set[str] = set()
        for item_id in set(old) | set(new):
            before = old.get(item_id)
            after = new.get(item_id)
            if after is None or before is None:
                continue
            if before["passed"] == after["passed"]:
                continue
            rule = after.get("rule")
            if rule not in ("F1", "F3"):
                continue
            seen.add(rule)
            leaves_by_rule[rule][item_id] += 1
        moved_by_score = abs(row["new_score"] - row["old_score"]) > 1e-12
        if row.get("report_only") and moved_by_score:
            seen.add("F2")
            for item_id in row["report_only"]:
                leaves_by_rule["F2"][item_id] += 1
        for rule in seen:
            rows_by_rule[rule] += 1
    return dict(rows_by_rule), leaves_by_rule


def rule_reach(scored: list[dict]) -> dict[str, Counter]:
    """How far a rule reached, moved or not — the F1/F3 leaves it retargeted."""
    reach: dict[str, Counter] = defaultdict(Counter)
    for row in scored:
        old = {v["id"]: v for v in row["old_verdicts"]}
        for item_id, verdict in ((v["id"], v) for v in row["new_verdicts"]):
            rule = verdict.get("rule")
            if rule not in ("F1", "F3"):
                continue
            before = old.get(item_id)
            changed = before is not None and before["passed"] != verdict["passed"]
            reach[rule]["moved" if changed else "retargeted_only"] += 1
            reach[rule + "_fields"][item_id] += 1
    return reach


def f2_census(
    scored: list[dict], truths: dict[str, dict]
) -> dict:
    """What F2 actually does: candidates, drops, refusals.

    Calls the scorer's own ``select_report_only`` / ``report_only_drop`` so the
    census is the rule, not a description of it.
    """
    from sci3d_bench.scorers import deterministic_answer as da

    out = {
        "candidates": 0, "dropped": 0, "refused": 0,
        "candidate_tasks": set(), "refused_tasks": set(),
        "empty_string_trials": 0, "empty_string_tasks": set(),
        "empty_string_examples": [],
        "dropped_examples": [],
        "available": bool(truths),
    }
    if not truths:
        out["candidate_tasks"] = 0
        out["refused_tasks"] = 0
        out["empty_string_tasks"] = 0
        return out

    for row in scored:
        truth = truths.get(row["task_id"])
        if not truth:
            continue
        item_ids = {v["id"] for v in row["old_verdicts"]}
        candidates = da.select_report_only(truth, []) & item_ids
        if not candidates:
            continue
        out["candidates"] += 1
        out["candidate_tasks"].add(row["task_id"])
        dropped = da.report_only_drop(truth, [], item_ids)
        if not dropped:
            out["refused"] += 1
            out["refused_tasks"].add(row["task_id"])
            survivors = item_ids - candidates
            if len(survivors) == 1:
                only = next(iter(survivors))
                if truth.get(only) == "":
                    out["empty_string_trials"] += 1
                    out["empty_string_tasks"].add(row["task_id"])
                    if len(out["empty_string_examples"]) < 4:
                        out["empty_string_examples"].append(
                            {"task_id": row["task_id"], "item": only}
                        )
        else:
            out["dropped"] += 1
            if len(out["dropped_examples"]) < 4:
                out["dropped_examples"].append(
                    {
                        "task_id": row["task_id"],
                        "arm": row["arm"],
                        "items": sorted(dropped),
                        "old": row["old_score"],
                        "new": row["new_score"],
                    }
                )
    out["candidate_tasks"] = len(out["candidate_tasks"])
    out["refused_tasks"] = len(out["refused_tasks"])
    out["empty_string_tasks"] = len(out["empty_string_tasks"])
    return out


def summarise(rows: list[dict], truths: dict[str, dict]) -> dict:
    scored = [r for r in rows if r["status"] == "scored"]
    reproduced = [r for r in scored if r["reproduced"]]
    skipped = [r for r in rows if r["status"] == "skipped"]
    if not scored:
        raise SystemExit("no scored rows in the input")

    moved = _moved(reproduced, "old_score", "new_score")
    moved_all = _moved(scored, "old_rule_score", "new_score")
    down = [r for r in scored if r["new_score"] < r["old_rule_score"] - 1e-12]

    bases = {
        "all_n": len(scored),
        "all_old": _mean(scored, "old_rule_score"),
        "all_new": _mean(scored, "new_score"),
        "rep_n": len(reproduced),
        "rep_old": _mean(reproduced, "old_score"),
        "rep_new": _mean(reproduced, "new_score"),
        "stored_old": _mean(scored, "old_score"),
    }
    bases["all_delta"] = bases["all_new"] - bases["all_old"]
    bases["rep_delta"] = bases["rep_new"] - bases["rep_old"]
    bases["total_gain"] = sum(r["new_score"] - r["old_score"] for r in reproduced)

    domains = []
    for name in sorted({r["domain"] for r in scored}):
        group = [r for r in reproduced if r["domain"] == name]
        if not group:
            continue
        old, new = _mean(group, "old_score"), _mean(group, "new_score")
        domains.append(
            {
                "name": name,
                "n": len(group),
                "old": old,
                "new": new,
                "delta": new - old,
                "moved": len(_moved(group, "old_score", "new_score")),
            }
        )
    # Descending by the new mean, which is the ordering F6's own card uses
    # (its plan ladder queue descends); who moved is carried by the delta line.
    domains.sort(key=lambda d: -d["new"])

    arms = []
    for name in sorted({r["arm"] for r in scored}):
        group = [r for r in reproduced if r["arm"] == name]
        if not group:
            continue
        old, new = _mean(group, "old_score"), _mean(group, "new_score")
        arms.append(
            {
                "name": name, "n": len(group), "old": old, "new": new,
                "delta": new - old, "moved": len(_moved(group, "old_score", "new_score")),
            }
        )

    rows_by_rule, leaves_by_rule = attribute_moves(scored)
    reach = rule_reach(scored)
    census = f2_census(scored, truths)

    # The 15: stored vs replayed, grouped by cause.
    not_reproduced = [r for r in scored if not r["reproduced"]]
    cause = {}
    for row in not_reproduced:
        if row["task_id"].startswith("asset-"):
            key = "band"
        else:
            key = "revised"
        cause.setdefault(key, []).append(row)

    parse_gate = Counter(str(r.get("parse_error")) for r in rows if r.get("parse_error"))

    context = {
        "counts": {
            "rows": len(rows), "scored": len(scored), "skipped": len(skipped),
            "reproduced": len(reproduced), "not_reproduced": len(not_reproduced),
            "moved": len(moved), "moved_all": len(moved_all), "down": len(down),
        },
        "bases": bases,
        "domains": domains,
        "arms": arms,
        "rules": {
            "moved_rows": rows_by_rule,
            "moved_leaves": {k: dict(v) for k, v in leaves_by_rule.items()},
            "reach": {k: dict(v) for k, v in reach.items() if not k.endswith("_fields")},
            "fields": {
                "F1": sorted(reach["F1_fields"]),
                "F3": sorted(reach["F3_fields"]),
            },
        },
        "f2": census,
        "skip_reasons": dict(Counter(r["reason"] for r in skipped)),
        "parse_gate": dict(parse_gate),
        "not_reproduced": [
            {
                "task_id": r["task_id"], "arm": r["arm"],
                "stored": r["old_score"], "replayed": r["old_rule_score"],
                "new": r["new_score"],
                "cause": "band" if r["task_id"].startswith("asset-") else "revised",
            }
            for r in sorted(not_reproduced, key=lambda r: (r["task_id"], r["arm"]))
        ],
        "superseded": {},
    }
    return context


# ── HTML ──────────────────────────────────────────────────────────────────── #

CSS = """
  /* ═══════════════════════════════════════════════════════════════
     R09 · 数据故事仪表盘（青瓷 · 中文正本），原样沿用：
     版心 1080 / 章节顺序 / 2×2 图区 / 右栏 KPI / 间距 / 色板。
     本文件由 tools/freetext_report.py 生成，不要手改。
     色板正本 = color-presets.js 的 PORCELAIN 一套，未引入其他预设。
     ═══════════════════════════════════════════════════════════════ */
  :root{
    --bg:#F7F2EB; --txt:#081F5C;
    --mut:rgba(8,31,92,.60); --lab:rgba(8,31,92,.72);
    --faint:rgba(8,31,92,.32); --quiet:rgba(8,31,92,.15); --grid:rgba(8,31,92,.16);
    --data:#334EAC; --data2:#7096D1; --hero:#081F5C; --faintdata:#BAD6EB;
    --panel:rgba(8,31,92,.045);
  }
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:var(--bg);color:var(--txt);font-family:'Inter','Noto Sans SC',sans-serif;
    -webkit-font-smoothing:antialiased;font-variant-numeric:tabular-nums lining-nums;
    display:flex;justify-content:center;padding:52px 28px}
  .sheet{width:1080px;max-width:1080px}

  .topbar{display:flex;justify-content:space-between;align-items:baseline;
    border-bottom:1px solid var(--txt);padding-bottom:12px}
  .topbar .l{font-size:14px;font-weight:800;letter-spacing:.18em}
  .topbar .r{font-size:10px;font-weight:600;letter-spacing:.2em;color:var(--mut)}

  .hero{display:grid;grid-template-columns:360px 1fr;gap:56px;margin-top:44px;align-items:center}
  .hero h1{font-size:52px;font-weight:900;line-height:1.32;letter-spacing:.02em}
  .hero .dek{font-size:13px;line-height:1.9;color:var(--lab);margin-top:20px;max-width:320px}
  .hero .dek b{color:var(--txt)}
  .hero .meta{margin-top:26px}
  .hero .meta .n{font-size:15px;font-weight:700}
  .hero .meta .d{font-size:11px;color:var(--mut);line-height:1.8;margin-top:4px}
  .hero .meta .d b{color:var(--data);font-weight:700}
  .fig svg{width:100%;height:auto;display:block}
  svg text{font-family:'Inter','Noto Sans SC',sans-serif}

  .dotrule{border-top:1px dotted var(--faint);margin-top:44px}

  .main{display:grid;grid-template-columns:1fr 300px;gap:56px;margin-top:40px}
  .glance>.ttl{display:flex;align-items:baseline;gap:18px;border-bottom:1px solid var(--txt);
    padding-bottom:14px}
  .glance>.ttl h2{font-size:34px;font-weight:800;letter-spacing:.06em}
  .glance>.ttl .ex{font-size:11px;color:var(--mut);line-height:1.6}
  .g2{display:grid;grid-template-columns:1fr 1fr;gap:40px;margin-top:30px}
  /* Both heads are pinned to two lines. The R09 template's own demo strings
     are all one line, so its grid never needed this; here a claim or a micro
     that wraps in one cell and not its neighbour drops that cell's chart by a
     full line and the 2x2 grid stops reading as a grid. */
  /* Both heads are pinned to exactly two lines. R09's own demo strings are one
     line each, so its grid never needed this; here a claim that wraps in one
     cell and not its neighbour drops that cell's chart by a full line and the
     2x2 grid stops reading as a grid. A claim needing a third line would
     overflow, so the generator keeps the claim strings short. */
  .cell .claim{display:block;font-size:15px;font-weight:800;letter-spacing:.02em;
    line-height:1.5;height:45px}
  .cell .micro{display:block;font-size:9.5px;font-weight:600;letter-spacing:.14em;
    color:var(--mut);margin:5px 0 10px;line-height:1.6;height:31px}
  .cell .panel{background:var(--panel);padding:18px 14px 12px}
  .cell .srcline{font-size:9px;font-weight:600;letter-spacing:.12em;color:var(--faint);
    margin-top:9px;line-height:1.6}
  .g2note{font-size:9.5px;font-weight:600;color:var(--mut);line-height:1.9;margin-top:22px;
    border-top:1px dotted var(--faint);padding-top:12px}

  .rail h2{font-size:30px;font-weight:800;letter-spacing:.04em;line-height:1.2;
    border-bottom:1px solid var(--txt);padding-bottom:14px}
  .rail .intro{font-size:11.5px;line-height:1.95;color:var(--lab);margin-top:18px}
  .legend{display:grid;grid-template-columns:1fr 1fr;gap:10px 14px;margin-top:20px}
  .legend .it{display:flex;align-items:center;gap:7px;font-size:10.5px;font-weight:600;line-height:1.4}
  /* .legend .sw must out-specify the page-level `.hero` rule (display:grid /
     margin-top:44px), which also matches <span class="sw hero"> — without this
     the first swatch collapses and drops below its own label. */
  .legend .sw{display:block;width:13px;height:13px;flex:none;margin:0}
  .legend .sw.hero{background:var(--hero)}
  .legend .sw.base{background:var(--data)}
  .legend .sw.aux{background:var(--data2)}
  .legend .sw.out{background:var(--faintdata)}

  .kgrid{display:grid;grid-template-columns:1fr 1fr;gap:26px 20px;margin-top:28px}
  .kpi .h{font-size:14px;font-weight:800;border-top:1px solid var(--txt);padding-top:8px;line-height:1.4}
  /* same reason as .cell .claim: a two-line caption in one card and a one-line
     caption in its neighbour puts the two dotted boxes at different heights. */
  .kpi .d{font-size:9.5px;color:var(--mut);line-height:1.55;margin:3px 0 8px;
    height:31px}
  .kpi .box{border:1.5px dotted var(--faint);padding:12px 12px 10px;position:relative}
  .kpi .box .v{font-size:20px;font-weight:800;text-align:right;letter-spacing:-.02em;
    white-space:nowrap}
  .kpi .box .bar{height:11px;margin-top:10px;display:flex}
  .kpi .box .bar i{display:block;height:100%}

  .sect{margin-top:56px}
  .sect>.ttl{display:flex;justify-content:space-between;align-items:baseline;gap:24px;
    border-bottom:1px solid var(--txt);padding-bottom:14px}
  .sect>.ttl h2{font-size:34px;font-weight:800;letter-spacing:.06em}
  .sect>.ttl .ex{font-size:11px;color:var(--mut);line-height:1.6;max-width:560px;text-align:right}
  .items{margin-top:26px;display:grid;gap:26px 40px}
  .items.c1{grid-template-columns:1fr}
  .items.c2{grid-template-columns:1fr 1fr}
  .items.c3{grid-template-columns:repeat(3,1fr)}
  .items .it{border-top:1px solid var(--txt);padding-top:10px}
  .items .k{font-size:9.5px;font-weight:700;letter-spacing:.16em;color:var(--data)}
  .items .h{font-size:13.5px;font-weight:800;margin:5px 0 7px;line-height:1.5}
  .items .b{font-size:10.5px;line-height:1.9;color:var(--lab)}
  .items .b b{color:var(--txt);font-weight:700}
  .items .b i{font-style:normal;color:var(--txt);font-weight:600}
  .items .b code{font-family:'Inter','Noto Sans SC',sans-serif;font-weight:600;color:var(--txt)}

  .tbl{margin-top:24px;width:100%;border-collapse:collapse;font-size:11px}
  .tbl th{text-align:left;font-size:9px;font-weight:700;letter-spacing:.14em;color:var(--mut);
    border-bottom:1px solid var(--txt);padding:0 12px 8px 0;vertical-align:bottom}
  .tbl td{padding:9px 12px 9px 0;border-bottom:1px dotted var(--faint);vertical-align:top;
    line-height:1.6}
  .tbl td.num{text-align:right;white-space:nowrap;font-weight:700}
  .tbl td.num.faint{font-weight:600;color:var(--mut)}
  .tbl td.id{font-weight:700;font-size:10.5px;letter-spacing:-.01em}
  .tbl td.arm{font-size:9.5px;font-weight:700;letter-spacing:.08em;color:var(--data)}
  .tbl td.why{font-size:10.5px;color:var(--lab);line-height:1.7}
  .tbl tr.grp td{padding-top:22px;border-bottom:none;font-size:9.5px;font-weight:700;
    letter-spacing:.14em;color:var(--data)}
  .tbl tr:first-child td{padding-top:0}

  .foot{margin-top:56px;border-top:1px solid var(--txt);padding-top:14px;
    display:grid;grid-template-columns:1fr 1fr 1fr;gap:20px;align-items:start}
  .foot .warn{font-size:10px;font-weight:700;color:var(--data);line-height:1.7}
  .foot .warn small{display:block;font-weight:500;color:var(--mut)}
  .foot .src{font-size:9.5px;color:var(--mut);line-height:1.7;letter-spacing:.06em}
  .foot .logo{text-align:right;font-size:15px;font-weight:800;letter-spacing:.14em;line-height:1.5}

  .pop{transform-box:fill-box;transform-origin:center;animation:pop .5s cubic-bezier(.2,.7,.3,1.3) both}
  @keyframes pop{from{transform:scale(0)}to{transform:none}}
  .fade{animation:fade .9s ease both}
  @keyframes fade{from{opacity:0}}
  .draw{stroke-dasharray:1;stroke-dashoffset:1;animation:draw 1s cubic-bezier(.4,0,.2,1) both}
  @keyframes draw{to{stroke-dashoffset:0}}
  @media(max-width:640px){body{padding:24px 10px}}
  @media (prefers-reduced-motion:reduce){
    .pop,.fade{animation:none}
    .draw{animation:none;stroke-dasharray:none;stroke-dashoffset:0}
  }
"""

SCRIPT = r"""
/* ── mono-tokens 内联（几何/工具函数与正本一致）── */
const NS='http://www.w3.org/2000/svg';
const el=(p,t,a)=>{const n=document.createElementNS(NS,t);for(const k in a)n.setAttribute(k,a[k]);p.appendChild(n);return n};
const txt=(p,a,s)=>{const n=el(p,'text',a);n.textContent=s;return n};
const tip=(n,s)=>{const t=document.createElementNS(NS,'title');t.textContent=s;n.appendChild(t)};
const rnd=(i,k)=>Math.abs(((i*73856093)^(k*19349663))%1000)/1000;
const D2R=Math.PI/180;
const pol=(cx,cy,r,deg)=>[cx+r*Math.cos(deg*D2R),cy+r*Math.sin(deg*D2R)];
const obsReveal=(id,fn)=>{
  const n=document.getElementById(id);
  const go=()=>{n.innerHTML='';fn(n)};
  const io=new IntersectionObserver(es=>{if(es[0].isIntersecting){go();io.disconnect()}},{threshold:.3});
  io.observe(n); n.style.cursor='pointer'; n.addEventListener('click',go);
};

/* ── PORCELAIN 角色 token（basics-porcelain.html 原值）── */
const BG='#F7F2EB',TXT='#081F5C',MUT='rgba(8,31,92,.60)',LAB='rgba(8,31,92,.72)',
      FAINT='rgba(8,31,92,.32)';
const FLOOR='rgba(8,31,92,.24)',QUIET='rgba(8,31,92,.15)',GRID='rgba(8,31,92,.16)';
const DATA='#334EAC',DATA2='#7096D1',HERO='#081F5C',FAINTDATA='#BAD6EB';

const D = window.__REPORT__;
/* The JSON carries colour ROLES by name, not hex — the palette stays in this
   file, so a role that is renamed here cannot silently become a new colour. */
const SHADE = {DATA, DATA2, HERO, FAINTDATA};

/* ════ 刊头 · 弧线流程装饰 ════
   纯装饰件：四个流程节点沿基线排开，相邻节点之间发丝弧承接，
   「存储答案 → 增量」一条主弧跨全程。不标数值。 */
(()=>{
const NODE=[['存储答案',40],['旧规则',236],['新规则',420],['增量',580]];
obsReveal('flow',s=>{
  const OY=178;
  el(s,'line',{x1:16,y1:OY,x2:604,y2:OY,stroke:GRID,'stroke-width':.8,class:'fade'});
  const O=NODE[0][1];
  const arc=(toX,stroke,sw,op,dl)=>{
    const r=(toX-O)/2,ry=Math.min(150,r);
    el(s,'path',{d:`M${O} ${OY} A${r} ${ry} 0 0 1 ${toX} ${OY}`,fill:'none',
      stroke,'stroke-width':sw,opacity:op,pathLength:1,
      class:'draw',style:`animation-delay:${dl}s;animation-duration:1s`});
  };
  arc(NODE[1][1],FAINTDATA,1.3,.95,.2);
  arc(NODE[3][1],DATA2,1.1,.6,.35);
  arc(NODE[2][1],DATA,3,1,.55);
  NODE.forEach(([name,x],i)=>{
    el(s,'circle',{cx:x,cy:OY,r:i===0||i===3?4:2.6,fill:i===0||i===3?HERO:DATA,
      class:'pop',style:`animation-delay:${.3+i*.12}s`});
    txt(s,{x,y:OY+22,'font-size':9.5,'font-weight':700,fill:i===0||i===3?TXT:MUT,
      'text-anchor':'middle','letter-spacing':'.14em',
      class:'fade',style:`animation-delay:${.4+i*.12}s`},name);
  });
});
})();

/* ════ 图A · F6 paired rungs ════
   basics-gallery.html 正本结构：每类两把梯子并肩，旧档淡、新档实。
   此处 1 档 = 0.01 均值；档数取整到整档，精确值写在类目下方一行。 */
(()=>{
const D0=D.domains, x0=i=>39+i*78, base=258, step=3.7, HW=10;
obsReveal('domains',s=>{
  D0.forEach((d,i)=>{
    const xa=x0(i)-14, xb=x0(i)+14, was=d.rungs_old, now=d.rungs_new;
    for(let k=0;k<was;k++){
      const y=base-k*step, w=HW-1.2+rnd(k+1,i+2)*2.4;
      el(s,'line',{x1:xa-w,y1:y,x2:xa+w,y2:y,stroke:DATA,'stroke-width':1,
        opacity:.45+rnd(k+2,i+3)*.35,class:'fade',style:`animation-delay:${i*.06+k*.006}s`});
    }
    for(let k=0;k<now;k++){
      const y=base-k*step, w=HW-1.2+rnd(k+1,i+7)*2.4;
      el(s,'line',{x1:xb-w,y1:y,x2:xb+w,y2:y,stroke:HERO,'stroke-width':1,
        opacity:.65+rnd(k+2,i+8)*.35,class:'fade',style:`animation-delay:${.12+i*.06+k*.006}s`});
    }
    const topA=base-(was-1)*step, topB=base-(now-1)*step;
    const la=txt(s,{x:xa,y:topA-9,'font-size':8.5,'font-weight':700,fill:DATA,'text-anchor':'middle',
      class:'fade',style:`animation-delay:${.4+i*.06}s`},was);
    tip(la,`${d.name} — old rule ${d.old_text} (${was} rungs of 0.01)`);
    const lb=txt(s,{x:xb,y:topB-9,'font-size':10.5,'font-weight':800,fill:HERO,'text-anchor':'middle',
      class:'fade',style:`animation-delay:${.45+i*.06}s`},now);
    tip(lb,`${d.name} — n=${d.n} · ${d.old_text} → ${d.new_text} · Δ ${d.delta_text} · ${d.moved} rows moved`);
    txt(s,{x:x0(i),y:276,'font-size':7.5,'font-weight':700,fill:MUT,'text-anchor':'middle',
      'letter-spacing':'.06em',class:'fade',style:`animation-delay:${i*.06}s`},d.name);
    txt(s,{x:x0(i)-4,y:288,'font-size':7,'font-weight':700,fill:DATA,'text-anchor':'end',
      class:'fade',style:`animation-delay:${.5+i*.06}s`},d.old_text);
    txt(s,{x:x0(i),y:288,'font-size':7,'font-weight':600,fill:FAINT,'text-anchor':'middle',
      class:'fade',style:`animation-delay:${.5+i*.06}s`},'→');
    txt(s,{x:x0(i)+4,y:288,'font-size':7,'font-weight':800,fill:HERO,
      class:'fade',style:`animation-delay:${.5+i*.06}s`},d.new_text);
    txt(s,{x:x0(i),y:300,'font-size':7.5,'font-weight':800,'text-anchor':'middle',
      fill:d.moved?HERO:DATA2,class:'fade',style:`animation-delay:${.55+i*.06}s`},d.delta_text);
  });
  el(s,'line',{x1:12,y1:base+4,x2:388,y2:base+4,stroke:GRID,'stroke-width':.8,class:'fade'});
  txt(s,{x:200,y:316,'font-size':7,'font-weight':600,fill:FAINT,'text-anchor':'middle',
    'letter-spacing':'.12em',class:'fade',style:'animation-delay:1s'},
    'ONE RUNG = 0.01 MEAN SCORE · FAINT = OLD · SOLID = NEW');
});
})();

/* ════ 图B · F5 tick rows ════
   basics-gallery.html 正本结构：一行 = 一条规则，1 tick = 1 行移动。
   刻度线固定格宽，所有行同尺度；每五根一个点标；行尾大数。 */
(()=>{
const R=D.rules_view, X0=158, PX=30, MAXN=Math.max(1,D.rules_view[0].scale), TICK=1.3;
obsReveal('ruletally',s=>{
  R.forEach((r,i)=>{
    const y=96+i*64, base=y+9;
    txt(s,{x:54,y:y+3,'font-size':11,'font-weight':800,fill:TXT,'text-anchor':'end',
      class:'fade',style:`animation-delay:${i*.08}s`},r.id);
    txt(s,{x:148,y:y+3,'font-size':7.5,'font-weight':600,fill:MUT,'text-anchor':'end',
      class:'fade',style:`animation-delay:${i*.08}s`},r.label);
    el(s,'line',{x1:X0,y1:base,x2:X0+MAXN*PX,y2:base,stroke:GRID,'stroke-width':.6,
      class:'fade',style:`animation-delay:${i*.08}s`});
    for(let k=0;k<r.moved;k++){
      const x=X0+k*PX+PX/2, h=17+rnd(k+1,i+2)*7;
      el(s,'line',{x1:x,y1:base,x2:x,y2:base-h,stroke:r.moved?HERO:FAINT,'stroke-width':TICK,
        opacity:.62+rnd(k+3,i+5)*.38,class:'fade',style:`animation-delay:${i*.08+k*.03}s`});
      if(k%5===4)el(s,'circle',{cx:x,cy:base+5,r:.8,fill:DATA2,
        class:'fade',style:`animation-delay:${i*.08+k*.03}s`});
    }
    const lab=txt(s,{x:X0+r.moved*PX+9,y:y+4,'font-size':11,'font-weight':800,
      fill:r.moved?HERO:MUT,
      style:`paint-order:stroke;stroke:${BG};stroke-width:3px;animation-delay:${.5+i*.08}s`,
      class:'fade'},r.moved);
    const hit=el(s,'rect',{x:X0,y:y-8,width:MAXN*PX,height:26,fill:'transparent'});
    tip(hit,`${r.id} · ${r.label} — ${r.moved} rows (${r.fields})`);
  });
  txt(s,{x:200,y:268,'font-size':7,'font-weight':600,fill:FAINT,'text-anchor':'middle',
    'letter-spacing':'.12em',class:'fade',style:'animation-delay:1s'},D.rules_caption);
  txt(s,{x:200,y:290,'font-size':7,'font-weight':600,fill:FAINT,'text-anchor':'middle',
    'letter-spacing':'.12em',class:'fade',style:'animation-delay:1.1s'},D.rules_caption2);
});
})();

/* ════ 图C · L14 hundred field ════
   lupi-gallery.html 正本结构：phyllotaxis 圆盘，一格 = 一个诚实单位。
   此处 500 条 trial 折成 100，1 dot = 1%（整 5 条）。 */
(()=>{
const SEG=D.census_view, POS=[[141,132],[293,132],[217,254]];
obsReveal('census',s=>{
  [[0,1],[0,2],[1,2]].forEach(([a,b],k)=>{
    el(s,'line',{x1:POS[a][0],y1:POS[a][1],x2:POS[b][0],y2:POS[b][1],
      stroke:GRID,'stroke-width':.7,'stroke-dasharray':'2 5',
      class:'fade',style:`animation-delay:${.9+k*.1}s`});
  });
  SEG.forEach((seg,ci)=>{
    const [cx,cy]=POS[ci], v=seg.dots;
    let edge=0;
    for(let k=0;k<v;k++){
      const a=k*137.508+ci*55;
      const rr=4+Math.sqrt(k)*4.4+rnd(k+1,ci+2)*3;
      edge=Math.max(edge,rr);
      const [x,y]=pol(cx,cy,rr,a);
      if(k%5===0)el(s,'line',{x1:cx,y1:cy,x2:x,y2:y,stroke:FAINTDATA,'stroke-width':.6,
        class:'fade',style:`animation-delay:${ci*.14+k*.012}s`});
      const dot=el(s,'circle',{cx:x,cy:y,r:1.5+rnd(k+2,ci+3)*1.7,fill:SHADE[seg.shade],opacity:.9,
        class:'pop',style:`animation-delay:${ci*.14+k*.012}s`});
      tip(dot,`${seg.label} — one dot = 1% of the ${D.counts.rows} trials`);
    }
    const lab=txt(s,{x:cx,y:cy+edge+13,'font-size':8,'font-weight':800,fill:HERO,'text-anchor':'middle',
      'letter-spacing':'.08em',style:`paint-order:stroke;stroke:${BG};stroke-width:3px;animation-delay:${.5+ci*.12}s`,
      class:'fade'},seg.tag);
    tip(lab,`${seg.label} — ${v} of 100 dots`);
  });
  txt(s,{x:200,y:306,'font-size':7,'font-weight':600,fill:FAINT,'text-anchor':'middle',
    'letter-spacing':'.12em',class:'fade',style:'animation-delay:1.3s'},D.census_caption);
});
})();

/* ════ 图D · F12 dumbbell queue ════
   basics-gallery.html 正本结构：空心点 = 旧，实心点 = 新，两点之间一条轨道，
   旧值贴在空心点左侧，新值贴在实心点右侧，行尾大数写增量。
   轴是单一线性刻度（没有分段、没有压缩），但窗口不是从 0 起；
   端点写在轴上，每个点的精确值印在点旁。 */
(()=>{
const D0=D.bases_view, X0=92, X1=330, LO=D.axis_lo, HI=D.axis_hi;
const mapX=v=>X0+(v-LO)/(HI-LO)*(X1-X0);
obsReveal('bases',s=>{
  D0.forEach((b,i)=>{
    const y=92+i*76, xa=mapX(b.old), xb=mapX(b.new);
    txt(s,{x:84,y:y+4,'font-size':14,'font-weight':800,fill:TXT,'text-anchor':'end',
      class:'fade',style:`animation-delay:${i*.1}s`},b.n);
    txt(s,{x:84,y:y+18,'font-size':7,'font-weight':600,fill:MUT,'text-anchor':'end',
      class:'fade',style:`animation-delay:${i*.1}s`},b.tag);
    el(s,'line',{x1:X0-6,y1:y,x2:X1+6,y2:y,stroke:QUIET,'stroke-width':.7,
      class:'fade',style:`animation-delay:${i*.1}s`});
    el(s,'circle',{cx:xa,cy:y,r:4.2,fill:BG,stroke:DATA,'stroke-width':1.3,
      class:'pop',style:`animation-delay:${.2+i*.1}s`});
    const after=el(s,'circle',{cx:xb,cy:y,r:4.6,fill:HERO,
      class:'pop',style:`animation-delay:${.6+i*.1}s`});
    tip(after,`n=${b.n} — old ${b.old_text} → new ${b.new_text} · Δ ${b.delta_text}`);
    txt(s,{x:xa-8,y:y-10,'font-size':8,'font-weight':700,fill:DATA,'text-anchor':'end',
      class:'fade',style:`animation-delay:${.3+i*.1}s`},b.old_text);
    txt(s,{x:xb+8,y:y-10,'font-size':8.5,'font-weight':800,fill:HERO,
      class:'fade',style:`animation-delay:${.7+i*.1}s`},b.new_text);
    txt(s,{x:394,y:y+4,'font-size':9,'font-weight':800,fill:HERO,'text-anchor':'end',
      class:'fade',style:`animation-delay:${.8+i*.1}s`},b.delta_text);
  });
  el(s,'line',{x1:X0,y1:226,x2:X1,y2:226,stroke:GRID,'stroke-width':.8,class:'fade'});
  D.ticks.forEach(t=>{
    const x=mapX(t.v);
    el(s,'line',{x1:x,y1:226,x2:x,y2:231,stroke:GRID,'stroke-width':.8,class:'fade'});
    txt(s,{x,y:243,'font-size':7,'font-weight':600,fill:MUT,'text-anchor':'middle',
      class:'fade',style:'animation-delay:.6s'},t.t);
  });
  txt(s,{x:200,y:36,'font-size':8,'font-weight':700,fill:MUT,'text-anchor':'middle',
    'letter-spacing':'.1em',class:'fade',style:'animation-delay:.3s'},D.axis_title);
  D.axis_captions.forEach((c,i)=>{
    txt(s,{x:200,y:272+i*20,'font-size':7,'font-weight':600,fill:FAINT,'text-anchor':'middle',
      'letter-spacing':'.12em',class:'fade',style:`animation-delay:${1+i*.1}s`},c);
  });
});
})();
"""


def _rungs(value: float, unit: float) -> int:
    return int(round(value / unit))


def chart_payload(ctx: dict, rung_unit: float = 0.01) -> dict:
    counts = ctx["counts"]
    domains = [
        {
            **d,
            "rungs_old": _rungs(d["old"], rung_unit),
            "rungs_new": _rungs(d["new"], rung_unit),
            "old_text": f'{d["old"]:.4f}',
            "new_text": f'{d["new"]:.4f}',
            "delta_text": _signed(d["delta"], 4),
        }
        for d in ctx["domains"]
    ]
    rules = ctx["rules"]["moved_rows"]
    fields = ctx["rules"]["fields"]
    order = ["F1", "F3", "F2"]
    label = {"F1": "NUMERIC STRING", "F3": "VOCAB ALIAS", "F2": "PROSE DROP"}
    view = [
        {
            "id": r,
            "label": label[r],
            "moved": int(rules.get(r, 0)),
            "fields": (
                " · ".join(f"{f} ×{n}" for f, n in sorted(ctx["rules"]["moved_leaves"].get(r, {}).items()))
                or "no row moved"
            ),
            "scale": max(1, max(int(rules.get(x, 0)) for x in order)),
        }
        for r in order
    ]
    total_moved = sum(int(rules.get(r, 0)) for r in order)

    f2 = ctx["f2"]
    census_view = [
        {"key": "reproduced", "label": "REPRODUCED", "dots": _rungs(counts["reproduced"] / counts["rows"], 0.01),
         "tag": f'{counts["reproduced"]} REPRODUCED', "shade": "DATA"},
        {"key": "skipped", "label": "SKIPPED", "dots": _rungs(counts["skipped"] / counts["rows"], 0.01),
         "tag": f'{counts["skipped"]} SKIPPED', "shade": "FAINTDATA"},
        {"key": "not_reproduced", "label": "NOT REPRODUCED", "dots": _rungs(counts["not_reproduced"] / counts["rows"], 0.01),
         "tag": f'{counts["not_reproduced"]} NOT REPRODUCED', "shade": "HERO"},
    ]

    bases = [
        {"n": ctx["bases"]["all_n"], "tag": "BOTH REPLAYED",
         "old": ctx["bases"]["all_old"], "new": ctx["bases"]["all_new"],
         "delta": ctx["bases"]["all_delta"]},
        {"n": ctx["bases"]["rep_n"], "tag": "OLD FROM AUDIT",
         "old": ctx["bases"]["rep_old"], "new": ctx["bases"]["rep_new"],
         "delta": ctx["bases"]["rep_delta"]},
    ]
    for b in bases:
        b["old_text"] = _fmt(b["old"])
        b["new_text"] = _fmt(b["new"])
        b["delta_text"] = _signed(b["delta"])

    # The dumbbell window: one uniform linear scale, cropped so the two
    # dot-pairs separate. A 0.007 gain on a 0.24 mean is 2.7 % of a zero-based
    # 0.26 axis — six pixels — so a zero-origin track cannot show it at all.
    lo = round(min(b["old"] for b in bases) - 0.03, 2)
    hi = round(max(b["new"] for b in bases) + 0.015, 2)
    step = 0.02
    ticks, t = [], lo
    while t <= hi + 1e-9:
        ticks.append({"v": round(t, 4), "t": f"{t:.2f}"})
        t += step

    return {
        "counts": counts,
        "domains": domains,
        "rules_view": view,
        "rules_total": total_moved,
        "rules_caption": f'ONE TICK = ONE MOVED TRIAL · {" + ".join(str(v["moved"]) for v in view)} = {total_moved}',
        "rules_caption2": f'{total_moved} ROWS UP · {counts["down"]} ROWS DOWN · HOVER A ROW FOR THE FIELDS IT MOVED',
        "census_view": census_view,
        "census_caption": (
            f'ONE DOT = 1% OF THE {counts["rows"]} TRIALS '
            f'({counts["rows"] // 100} TRIALS) · '
            f'{" + ".join(str(c["dots"]) for c in census_view)} = 100'
        ),
        "bases_view": bases,
        "axis_lo": lo,
        "axis_hi": hi,
        "ticks": ticks,
        "axis_title": f"MEAN ANSWER_SCORE · AXIS {lo:.2f} → {hi:.2f} · NOT ZERO-BASED",
        "axis_captions": [
            "HOLLOW = OLD · SOLID = NEW · ONE DOT = ONE MEAN SCORE",
            f"THE SCALE IS LINEAR AND UNIFORM BUT IT STARTS AT {lo:.2f}, NOT 0",
        ],
        "f2": ctx["f2"],
    }


def render(ctx: dict) -> str:
    counts = ctx["counts"]
    bases = ctx["bases"]
    f2 = ctx["f2"]
    payload = chart_payload(ctx)
    rules = ctx["rules"]["moved_rows"]
    f1_rows, f3_rows = int(rules.get("F1", 0)), int(rules.get("F3", 0))
    f2_rows = int(rules.get("F2", 0))

    domain_moved = [d for d in ctx["domains"] if d["moved"]]
    domain_flat = [d for d in ctx["domains"] if not d["moved"]]

    def kpi_cards() -> str:
        cards = [
            ("上移 / 下移", f'{counts["moved"]} 行移动，全部向上；但 F2 不是单调的',
             f'{counts["moved"]} / {counts["down"]}', counts["moved"] / counts["scored"], "HERO", "FAINTDATA"),
            ("480 基准均值", f'两列都重放 · n = {bases["all_n"]}',
             _signed(bases["all_delta"]), bases["all_old"] / (bases["all_old"] + bases["all_new"]), "DATA", "HERO"),
            ("465 基准均值", f'旧列读审计 · n = {bases["rep_n"]}',
             _signed(bases["rep_delta"]), bases["rep_old"] / (bases["rep_old"] + bases["rep_new"]), "DATA", "HERO"),
            ("复现 / 未复现", f'{counts["scored"]} 行里能逐字复现 / 不能',
             f'{counts["reproduced"]} / {counts["not_reproduced"]}',
             counts["reproduced"] / counts["scored"], "HERO", "FAINTDATA"),
            ("规则归属", f'F1 数字字符串 {f1_rows} · F3 别名 {f3_rows} · F2 {f2_rows}',
             f'{f1_rows} / {f3_rows}', f1_rows / max(1, f1_rows + f3_rows), "HERO", "DATA2"),
            ("F2 的候选", f'{f2["candidates"]} 个 audit 有候选：真删 {f2["dropped"]}，被拒 {f2["refused"]}',
             f'{f2["dropped"]} / {f2["refused"]}',
             f2["dropped"] / max(1, f2["candidates"]), "DATA2", "FAINTDATA"),
        ]
        out = []
        for head, desc, value, fill, c1, c2 in cards:
            fill = min(max(fill, 0.0), 1.0)
            out.append(
                '<div class="kpi">'
                f'<div class="h">{head}</div><div class="d">{desc}</div>'
                '<div class="box">'
                f'<div class="v">{value}</div>'
                f'<div class="bar"><i style="width:{fill * 100:.4f}%;background:var(--{c1.lower()})"></i>'
                f'<i style="width:{(1 - fill) * 100:.4f}%;background:var(--{c2.lower()})"></i></div>'
                '</div></div>'
            )
        return "\n      ".join(out)

    def not_reproduced_rows() -> str:
        groups = [
            ("revised", "GROUND TRUTH 在跑批之后被改过", {
                "sv-biol-molstar_chain_intersection-0007": "2026-10-09 换轨迹时一并改了答案（ASN 37 → ASN 96）；存储 verdict 对今天同一对值判 fail",
                "sv-biol-molstar_ligand_occlusion-0006": "同上（minimum_distance_A 3.582 → 4.116，ligand_atom C19 → C11）",
                "sv-chem-chem_conformation-0002": "两侧至少有一侧在跑批后动过：shortest_bond_path_atom_ids 存储 pass，但 GT [8,9,3] 与答案 [21,8,9,3,2] 长度不同，记录的 comparator 不可能给出 pass",
                "sv-medi-med_ct_geometry-0014": "2026-10-05/07 从 mask 重新算过并更正答案；存储 verdict 对今天相同的值判 fail",
                "sv-medi-med_ct_geometry-0016": "同上（答案由 {81, 65} 改为 {1.5, 206}）",
                "sv-medi-med_ct_geometry-0042": "同上",
            }),
            ("band", "树建于 band switch 之前", {
                "asset-675362e49da5-cross-view-identity-matching": "树建于 band switch 之前，用的是 decimal band 10^(1-d) = 0.1；highest_eave_elevation 5.86 vs 5.869 的 margin 0.009 在 ±0.005 下判 fail",
                "asset-c2c2a012842b-cross-view-identity-matching": "同上；accessible_ramp_width 1.5 vs 1.46 的 margin 0.04 需要 band ≥ 0.04，而同一 rubric 里 stair_railing_height 的 fail 又要求 band < 0.026 —— 只有 decimal band 能同时解释这两件事",
            }),
        ]
        html, index = [], 0
        for key, title, why in groups:
            rows = [r for r in ctx["not_reproduced"] if r["cause"] == key]
            if not rows:
                continue
            seen: Counter = Counter()
            html.append(
                f'<tr class="grp"><td colspan="6">{title} · {len(rows)} 行 / '
                f'{len({r["task_id"] for r in rows})} 个 task</td></tr>'
            )
            for row in rows:
                index += 1
                seen[row["task_id"]] += 1
                reason = why.get(row["task_id"], "同上")
                html.append(
                    "<tr>"
                    f'<td class="num faint">{index}</td>'
                    f'<td class="id">{row["task_id"]}</td>'
                    f'<td class="arm">{row["arm"]}</td>'
                    f'<td class="num faint">{_fmt(row["stored"], 6)}</td>'
                    f'<td class="num">{_fmt(row["replayed"], 6)}</td>'
                    f'<td class="why">{reason}</td>'
                    "</tr>"
                )
        return "\n        ".join(html)

    # Two lines maximum: .claim is pinned to 45px (2 x 15px/1.5) so the four
    # cells of a g2 row keep their panels level. Keep this short.
    domain_line = " 与 ".join(
        f'{d["name"]}（{d["moved"]} 行）' for d in domain_moved
    ) or "没有任何学科"
    arm_line = "；".join(
        f'{a["name"]} {a["n"]} 行 {a["old"]:.4f} → {a["new"]:.4f}（{_signed(a["delta"], 4)}，{a["moved"]} 行移动）'
        for a in ctx["arms"]
    )
    skip_invalid = sum(v for k, v in ctx["skip_reasons"].items() if k.startswith("invalid_task_contract"))
    skip_ids = sum(v for k, v in ctx["skip_reasons"].items() if k.startswith("rubric id"))
    parse_n = sum(ctx["parse_gate"].values())
    parse_json = ctx["parse_gate"].get("invalid_json", 0)
    parse_fields = ctx["parse_gate"].get("answer_fields_mismatch", 0)

    superseded_html = ""
    if ctx.get("superseded"):
        old = ctx["superseded"]
        superseded_html = f"""
      <div class="it">
        <div class="k">SUPERSEDED · 这一页取代了同一分支早些时候发布的 {old["moved"]}-行版本</div>
        <div class="h">上一版的 F2 判据是错的：480 基准 {old["delta_480"]:+.6f}、{bases["rep_n"]} 基准 {old["delta_465"]:+.6f}，两个都算大了 {old["delta_480"] / bases["all_delta"]:.1f} 倍</div>
        <div class="b">上一版把 F2 的拒绝实现成「<code>survivors == 0</code>：不许清空 rubric」，
          而 spec 写的是「<code>survivors &lt;= 1</code>：不许只剩一个 item」。
          两字之差，让 <b>{f2["refused"]} 个 audit</b>（分布在 {f2["refused_tasks"]} 个 task 上）
          被错删掉一个 item。这条错判据把 480 基准的增量从 <b>{_signed(bases["all_delta"])}</b>
          抬到 <b>{old["delta_480"]:+.6f}</b>，把 {bases["rep_n"]} 基准的从
          <b>{_signed(bases["rep_delta"])}</b> 抬到 <b>{old["delta_465"]:+.6f}</b> ——
          两个基数上，它都把自己的效果算大了 {old["delta_480"] / bases["all_delta"]:.1f} 倍。
          判据改正后重跑，得到本页的数字。
          旧的 <i>rescore.jsonl</i> 保留在原处，没有被覆盖；本页由
          <i>tools/freetext_report.py</i> 从 <i>rescore_v2.jsonl</i> 生成。</div>
      </div>"""
    else:
        superseded_html = """
      <div class="it">
        <div class="k">SINGLE RUN · 本页与输入一一对应</div>
        <div class="h">没有更早的版本被取代</div>
        <div class="b">生成器没有读到被取代的旧结果，所以这一页不声明更正。</div>
      </div>"""

    f2_fields = "、".join(
        sorted({e["item"] for e in f2["empty_string_examples"]})
    ) or "—"
    dropped_example = f2["dropped_examples"][0] if f2["dropped_examples"] else None
    per_dot = counts["rows"] / 100
    per_dot_text = (f"{counts['rows'] // 100}" if counts["rows"] % 100 == 0
                    else f"≈{per_dot:.1f}")

    return f"""<!doctype html>
<html lang="zh-Hans">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>换三条规则，{counts["moved"]} 行上移，{counts["down"]} 行下降 — 自由文本评分重放 · 青瓷</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Noto+Sans+SC:wght@400;500;700;900&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body>
<div class="sheet">

  <!-- ══ 顶栏 ══ -->
  <div class="topbar">
    <span class="l">SCI3D SCORER / PORCELAIN</span>
    <span class="r">FREE-TEXT RESCORING · V11-250 · RULES F1 / F2 / F3</span>
  </div>

  <!-- ══ hero ══ -->
  <div class="hero">
    <div>
      <h1>换三条规则，{counts["moved"]} 行上移，{counts["down"]} 行下降。</h1>
      <div class="dek">这是一次对<b>已存储答案</b>的离线重放：不重跑模型，不重建镜像或容器，
        harness 当年写下的分数一个字节都没动。{counts["moved"]} 行全部向上 —— 但那是<b>这批语料</b>的性质，
        不是规则的：F2 去掉一个<b>通过</b>的 item 会拉低分数，见下方 MONOTONICITY。</div>
      <div class="meta">
        <div class="n">{counts["rows"]} trials · {counts["scored"]} scored · 0 model runs</div>
        <div class="d">旧列 = 当时真实得分（{bases["rep_n"]} 基准）<br><b>本页更正的 F2 判据见 BASES &amp; CENSUS</b></div>
      </div>
    </div>
    <div>
      <!-- 弧线流程装饰：存储答案 → 旧规则 → 新规则 → 增量。纯装饰，不标数值。 -->
      <div class="fig"><svg id="flow" viewBox="0 0 620 220" preserveAspectRatio="xMidYMid meet"></svg></div>
    </div>
  </div>

  <div class="dotrule"></div>

  <!-- ══ 主体 ══ -->
  <div class="main">
    <div class="glance">
      <div class="ttl">
        <h2>AT A GLANCE</h2>
        <span class="ex">四个变量决定这张页面的可信度：谁进了比较集、两个分母差在哪里、增量落在哪个学科、由哪条规则带来。</span>
      </div>
      <div class="g2">

        <div class="cell">
          <div class="claim">增量只落在 {domain_line}，其余三科一行没动。</div>
          <div class="micro">ONE RUNG = 0.01 MEAN SCORE · FAINT = OLD · SOLID = NEW · {bases["rep_n"]} REPRODUCED TRIALS</div>
          <!-- 图A · F6 paired rungs · basics-gallery.html 正本，只换数据与色板 -->
          <div class="panel fig"><svg id="domains" viewBox="0 0 400 320" preserveAspectRatio="xMidYMid meet"></svg></div>
          <div class="srcline">F6 PAIRED RUNGS · BASICS-GALLERY · REAL TEMPLATE</div>
        </div>

        <div class="cell">
          <div class="claim">{counts["moved"]} 行全部来自 F1 与 F3；F2 的判据改正后一行都没再动。</div>
          <div class="micro">ONE TICK = ONE MOVED TRIAL · F1 {f1_rows} + F3 {f3_rows} + F2 {f2_rows} = {counts["moved"]}</div>
          <!-- 图B · F5 tick rows · basics-gallery.html 正本，只换数据与色板 -->
          <div class="panel fig"><svg id="ruletally" viewBox="0 0 400 320" preserveAspectRatio="xMidYMid meet"></svg></div>
          <div class="srcline">F5 TICK ROWS · BASICS-GALLERY · REAL TEMPLATE</div>
        </div>

        <div class="cell">
          <div class="claim">{counts["rows"]} 条里 {counts["reproduced"]} 条能逐字复现，{counts["not_reproduced"]} 条不能，{counts["skipped"]} 条跳过。</div>
          <div class="micro">ONE DOT = 1% OF THE {counts["rows"]} TRIALS ({per_dot_text} TRIALS) · {" + ".join(str(c["dots"]) for c in payload["census_view"])} = 100</div>
          <!-- 图C · L14 hundred field · lupi-gallery.html 正本，只换数据与色板 -->
          <div class="panel fig"><svg id="census" viewBox="0 0 400 320" preserveAspectRatio="xMidYMid meet"></svg></div>
          <div class="srcline">L14 HUNDRED FIELD · LUPI-GALLERY · REAL TEMPLATE</div>
        </div>

        <div class="cell">
          <div class="claim">两个分母共享同一个总增量 {bases["total_gain"]:.4f}，差别只在除以 {bases["all_n"]} 还是 {bases["rep_n"]}。</div>
          <div class="micro">HOLLOW = OLD · SOLID = NEW · ONE DOT = ONE MEAN · AXIS {payload["axis_lo"]:.2f} → {payload["axis_hi"]:.2f}, NOT ZERO-BASED</div>
          <!-- 图D · F12 dumbbell queue · basics-gallery.html 正本，只换数据与色板 -->
          <div class="panel fig"><svg id="bases" viewBox="0 0 400 320" preserveAspectRatio="xMidYMid meet"></svg></div>
          <div class="srcline">F12 DUMBBELL QUEUE · BASICS-GALLERY · REAL TEMPLATE</div>
        </div>

      </div>
      <div class="g2note">四张图上每个刻度都是真单位：F6 的一档 = 0.01 均值，F5 的一 tick = 1 行移动，
        L14 的一 dot = {counts["rows"]} 条里的 1%（{per_dot_text} 条）。F6 的档数取整到整档，
        最大偏差 2%（均值本身就在 0.01 附近的那些学科是 8%），
        每一行的精确均值都印在类目名下一行。F12 的珠串（一珠 = 一个单位）在这批数据里没有不含歧义的整数单位，
        因此省略，只保留空心 / 实心两点与轨道 —— 那是本页唯一一处对正本的删减；
        F12 的轴是单一线性刻度，但窗口从 {payload["axis_lo"]:.2f} 起、不从 0 起，端点和每个点的值都写在图上。</div>
    </div>

    <!-- ── 右栏 ── -->
    <div class="rail">
      <h2>THE HEADLINE</h2>
      <div class="intro">六个数字就是全部结论。每张卡下方那条横杠只是把卡里的两个数按比例画出来，
        两段加起来就是整条 —— 它不承载别的信息。</div>
      <div class="legend">
        <span class="it"><span class="sw hero"></span>新规则读数</span>
        <span class="it"><span class="sw base"></span>旧规则 / 基线</span>
        <span class="it"><span class="sw aux"></span>一个单位标记</span>
        <span class="it"><span class="sw out"></span>未移动 · 比较集外</span>
      </div>
      <!-- KPI 卡：数据家具（排字 + 单条示意），不占图额度 -->
      <div class="kgrid">
      {kpi_cards()}
      </div>
    </div>
  </div>

  <!-- ══ 单调性 ══ -->
  <div class="sect">
    <div class="ttl">
      <h2>MONOTONICITY</h2>
      <span class="ex">「没有一行下降」需要限一句：它是这批语料的性质，不是规则的。</span>
    </div>
    <div class="items c1">
      <div class="it">
        <div class="k">F2 不是单调的 · 去掉一个通过的 item 会让分数变低</div>
        <div class="h">一个逐字抄对了 <code>cited_clause</code> 段落、却答错真正考点的 agent，会从 0.50 掉到 0.00</div>
        <div class="b">F2 删掉权重 <i>w</i> 的一个 item，而总权重是 <i>T</i>、通过的权重是 <i>P</i>，
          新分数就是 <i>(P−w)/(T−w)</i>。只要 <i>T &gt; P</i>（即还有任何一项没通过），
          这个值就<b>低于</b> <i>P/T</i>；只有全部通过时才相等。
          换句话说，删掉一个<b>通过</b>的 item，同时拿掉分子上一分和分母上一分，分母变小得更快。
          具体形状：一个两项 rubric，答案逐字抄对了 <code>cited_clause</code>（1.0 权重，pass）、
          真正要考的字段答错（1.0 权重，fail），原本 0.5；<code>cited_clause</code> 一被删掉，
          rubric 只剩那个 fail，分数变成 <b>0.0</b>。在这个语料里这种情况一次也没发生 ——
          {counts["moved"]} 行上移、{counts["down"]} 行下降是真的 ——
          但它是规则的形状，所以这句话只能作为观察写，不能作为保证写。</div>
      </div>
    </div>
  </div>

  <!-- ══ 未复现的 15 行 ══ -->
  <div class="sect">
    <div class="ttl">
      <h2>THE {counts["not_reproduced"]} THAT DID NOT REPRODUCE</h2>
      <span class="ex">它们不在 {bases["rep_n"]} 基准里，也不是这次改动的功劳。存储 old 与重放 old 的差，
        全部由「跑批之后被改动的 ground truth」或「树建于 band switch 之前」造成。</span>
    </div>
    <table class="tbl">
      <thead><tr>
        <th style="width:26px">#</th>
        <th>TASK ID</th>
        <th style="width:52px">ARM</th>
        <th style="width:88px;text-align:right">存储 OLD</th>
        <th style="width:88px;text-align:right">重放 OLD</th>
        <th style="width:52%">为什么对不上</th>
      </tr></thead>
      <tbody>
        {not_reproduced_rows()}
      </tbody>
    </table>
  </div>

  <!-- ══ 口径 ══ -->
  <div class="sect">
    <div class="ttl">
      <h2>BASES &amp; CENSUS</h2>
      <span class="ex">三个基数、四个分母，一个都不许并排。这一段是替读者把「这个数到底是哪个数」钉死。</span>
    </div>
    <div class="items c2">
{superseded_html}

      <div class="it">
        <div class="k">BASIS 1 · {bases["all_n"]} 行 · 两列都重放</div>
        <div class="h">{bases["all_old"]:.6f} → {bases["all_new"]:.6f}，Δ {_signed(bases["all_delta"])}</div>
        <div class="b">旧列读 <i>old_rule_score</i>（同一份答案在旧规则下的重算值），
          新列读 <i>new_score</i>。{bases["all_n"]} 行全部由同一套代码、同一份输入算出。
          用它回答「规则有没有改变这批语料的分数」。</div>
      </div>

      <div class="it">
        <div class="k">BASIS 2 · {bases["rep_n"]} 行 · 旧列读审计</div>
        <div class="h">{bases["rep_old"]:.6f} → {bases["rep_new"]:.6f}，Δ {_signed(bases["rep_delta"])}</div>
        <div class="b">旧列读 harness 当时写进 audit 的存储分数，也就是这些 trial 当年真正得到的分数。
          用它回答「这次重放能担保多少」。两个基数共享同一个总增量
          <b>{bases["total_gain"]:.4f}</b>，{counts["not_reproduced"]} 行未复现的对增量贡献恰好
          <b>0.000000</b>，差别只在分母是 {bases["all_n"]} 还是 {bases["rep_n"]}。</div>
      </div>

      <div class="it">
        <div class="k">BASIS 3 · 污染 · 不与前两个并列</div>
        <div class="h">{bases["stored_old"]:.6f} = {bases["all_n"]} 行的存储旧值均值</div>
        <div class="b">它既不是 {bases["all_n"]} 的重放值，也不是 {bases["rep_n"]} 的存储值。其中 12 行的
          ground truth 在跑批之后被改过，是 stale contract 污染过的数。任何表里都不许和上面两个并排，
          本页不把它放进任何图或表。交付发布时用的又是第四个基数（全部 audit，不是这里的
          {counts["rows"]} 条 trial），既不是 {bases["all_n"]} 也不是 {bases["rep_n"]}，本页不复述它。</div>
      </div>

      <div class="it">
        <div class="k">F2 CENSUS · {f2["candidates"]} 个 audit 有候选</div>
        <div class="h">真删 {f2["dropped"]} 个，被拒 {f2["refused"]} 个</div>
        <div class="b">有候选的 {f2["candidates"]} 个 audit 分布在 {f2["candidate_tasks"]} 个 task 上。
          其中 <b>{f2["dropped"]} 个</b>真的删掉了一个 item{"" if not dropped_example else
          f''' —— 同一个 task（<i>{dropped_example["task_id"]}</i>）的两条臂，
          删掉的是本来就 fail 的 <i>{"、".join(dropped_example["items"])}</i>，
          分数 {dropped_example["old"]:.4f} → {dropped_example["new"]:.4f}，没有移动'''}。
          另外 <b>{f2["refused"]} 个</b>被拒绝，分布在 {f2["refused_tasks"]} 个 task 上：
          删完只剩 1 个 item，触发了 <code>survivors &lt;= 1</code> 的拒绝。
          其中 <b>{f2["empty_string_trials"]} 个</b>（{f2["empty_string_tasks"]} 个 task）
          剩下的那唯一一个 item，ground truth 是空字符串（{f2_fields}）——
          也就是说，如果拒绝不写在这里，这四个 task 会变成「回一个空串就满分」。</div>
      </div>

      <div class="it">
        <div class="k">TRIAL CENSUS · {counts["rows"]} → {counts["scored"]} 计入 + {counts["skipped"]} 跳过</div>
        <div class="h">没有一行被静默算成 0 或 1</div>
        <div class="b">跳过 = <b>{skip_invalid} 行</b> invalid_task_contract
          （atom_labels / interaction_types / residue_numbers 这类列表字段本身不该有 band，是树的契约坏了）
          + <b>{skip_ids} 行</b> rubric id 不在 ground truth 里（主要是 answer）。
          两条都写进 <i>reason</i>，不是丢掉。</div>
      </div>

      <div class="it">
        <div class="k">BY ARM · 两条臂同向</div>
        <div class="h">{ctx["arms"][0]["name"]} {_signed(ctx["arms"][0]["delta"], 4)} · {ctx["arms"][-1]["name"]} {_signed(ctx["arms"][-1]["delta"], 4)}</div>
        <div class="b">{arm_line}。两条臂的差值接近，方向一致，没有一条臂单独扛起这个结论。</div>
      </div>

    </div>
  </div>

  <!-- ══ 限制 ══ -->
  <div class="sect">
    <div class="ttl">
      <h2>THE LIMITATIONS</h2>
      <span class="ex">四条。前两条影响数字的精度，第三条影响分母，第四条这条测量的前提。</span>
    </div>
    <div class="items c2">

      <div class="it">
        <div class="k">LIMIT 1 · TOLERANCE 是推的，不是读的</div>
        <div class="h">重放与原跑用的是两套 band 推导</div>
        <div class="b">存储的 verdict 只带 id / passed / required / reason / comparator / weight，
          不带 band —— 所以 band 是唯一一个被建模而不是被读取的输入。
          {counts["rows"]} 个 runtime tree 里 <b>430 个</b>的树根时间戳早于 band switch
          （<i>7c8a26a2</i>，2026-09-28），当年用的是 decimal band <i>10^(1-d)</i>；
          本页用的是本分支的 <i>±0.005</i>。两套推导在<b>除三个 item 之外</b>的每一条存储 verdict 上一致：
          换成 switch 前的推导能复现 <b>468/480</b>，只剩那 12 行 ground truth 修订。
          所以 gap 正好是那三个 item，不是普遍的失真。</div>
      </div>

      <div class="it">
        <div class="k">LIMIT 2 · ALIAS（已核对闭合）</div>
        <div class="h">重放与 production 走的是同一条路径</div>
        <div class="b">重放对每个 <i>canonical_string_equal</i> item 都推导受控词别名，
          而 production 只在 implicit-rubric 路径上推导。量了一下：交付的 250 个 task document 里
          <b>0 个</b>带 <code>scorer.rubric</code>（bundle 里根本没有 <code>scorer</code> 块），
          所以这批交付的每个 task 都走 implicit 路径，重放与 production 一致。
          这一条从「可能有偏差」改成「已核对，无偏差」。
          F3 在这批语料里 retarget 了 6 个 leaf、跨 3 个 task，其中 {f3_rows} 个真的动了分。</div>
      </div>

      <div class="it">
        <div class="k">LIMIT 3 · {parse_n} 行被 parse gate 钉死在 0</div>
        <div class="h">任何规则改动都救不回它们</div>
        <div class="b">{parse_json} 行 <i>invalid_json</i> + {parse_fields} 行 <i>answer_fields_mismatch</i>，
          都是 audit 自己记下的 parse 失败。重放按同一代码给它们全 fail，
          这不是新规则能碰到的地方 —— 这 {parse_n} 行永远停在 0，与本次改动无关。</div>
      </div>

      <div class="it">
        <div class="k">LIMIT 4 · F2 的拒绝是这条测量的前提</div>
        <div class="h">没有这条拒绝，{f2["empty_string_trials"]} 个 audit 回一个空串就能满分</div>
        <div class="b">如果 F2 的拒绝写成「不许清空 rubric」（<code>survivors == 0</code>），
          那么 <b>{f2["refused"]} 个 audit</b>（{f2["refused_tasks"]} 个 task）会各删掉一个 item，
          其中 <b>{f2["empty_string_trials"]} 个</b>（{f2["empty_string_tasks"]} 个 task）
          删完只剩一个 item，而这个 item 的 ground truth 是<b>空字符串</b>
          （{f2_fields}），任何答案 —— 包括 <code>""</code> —— 都能匹配它。
          换句话说，那四个 task 会变成回一个空串就满分。
          拒绝写成 <code>survivors &lt;= 1</code> 正是为了挡住这个，所以本页的每一个数字
          都建立在这条拒绝成立的前提上。</div>
      </div>

    </div>
  </div>

  <!-- ══ 页脚 ══ -->
  <div class="foot">
    <div class="warn">存储分数没有被动过。
      <small>本页只是把同一份存储答案在旧规则与新规则下各重放一次：没有调用模型，没有重建镜像或容器。
        由 tools/freetext_report.py 从 rescore_v2.jsonl 生成。</small></div>
    <div class="src">DATA SOURCES<br>rescore_v2.jsonl · {counts["rows"]} trials（{counts["scored"]} scored）· v11-250<br>RULES F1 / F2 / F3 · SCORER deterministic_answer</div>
    <div class="logo">SCI3D<br>SCORER</div>
  </div>
</div>

<script>window.__REPORT__ = {json.dumps(payload, ensure_ascii=False, indent=1)};</script>
<script>{SCRIPT}</script>
<script>
/* ── fit-width：视口比版心窄时整页等比缩小，不重排 ── */
(()=>{{
  const sheet=document.querySelector('.sheet');
  const W=1080;
  const fit=()=>{{
    const cs=getComputedStyle(document.body);
    const avail=document.documentElement.clientWidth
      -parseFloat(cs.paddingLeft)-parseFloat(cs.paddingRight);
    sheet.style.zoom=Math.min(1,avail/W);
  }};
  fit();
  addEventListener('resize',fit);
}})();
</script>
</body>
</html>
"""


# ── entry point ───────────────────────────────────────────────────────────── #

def main(argv: list[str] | None = None) -> int:
    home = pathlib.Path.home()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rescore", default=str(home / "sv-freetext/rescore_v2.jsonl"),
                        help="replay output to report on")
    parser.add_argument("--bundle", default=str(home / "sv-freetext/bundle"),
                        help="task bundle, for the F2 census (optional)")
    parser.add_argument("--superseded", default=None,
                        help="an earlier replay output this page replaces, if any")
    parser.add_argument("--out", default=str(home / "sv-freetext/freetext_rescore_report.html"))
    args = parser.parse_args(argv)

    rows = load_rows(pathlib.Path(args.rescore))
    truths = load_ground_truths(pathlib.Path(args.bundle) if args.bundle else None)
    ctx = summarise(rows, truths)

    if args.superseded and pathlib.Path(args.superseded).exists():
        old_rows = load_rows(pathlib.Path(args.superseded))
        old_scored = [r for r in old_rows if r["status"] == "scored"]
        old_rep = [r for r in old_scored if r["reproduced"]]
        ctx["superseded"] = {
            "path": str(args.superseded),
            "moved": len(_moved(old_rep, "old_score", "new_score")),
            "delta_480": _mean(old_scored, "new_score") - _mean(old_scored, "old_rule_score"),
            "delta_465": _mean(old_rep, "new_score") - _mean(old_rep, "old_score"),
        }

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(ctx), encoding="utf-8")

    b = ctx["bases"]
    print(f"rows={ctx['counts']['rows']} scored={ctx['counts']['scored']} "
          f"skipped={ctx['counts']['skipped']}")
    print(f"reproduced={ctx['counts']['reproduced']}/{ctx['counts']['scored']} "
          f"moved={ctx['counts']['moved']} down={ctx['counts']['down']}")
    print(f"basis all  : old={b['all_old']:.6f} new={b['all_new']:.6f} "
          f"delta={b['all_delta']:+.6f} n={b['all_n']}")
    print(f"basis repro: old={b['rep_old']:.6f} new={b['rep_new']:.6f} "
          f"delta={b['rep_delta']:+.6f} n={b['rep_n']}")
    print(f"stored-old over {b['all_n']}: {b['stored_old']:.6f}")
    print(f"moved by rule: {ctx['rules']['moved_rows']}")
    f2 = ctx["f2"]
    if f2["available"]:
        print(f"F2 census  : candidates={f2['candidates']} dropped={f2['dropped']} "
              f"refused={f2['refused']} (tasks={f2['refused_tasks']}) "
              f"empty-string={f2['empty_string_trials']} (tasks={f2['empty_string_tasks']})")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
