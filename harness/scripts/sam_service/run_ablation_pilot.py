"""SAM-as-tool ablation PILOT driver (E1+E2, structured 3D paths).

Runs the §6 ablation matrix from
docs/superpowers/reports/2026-05-28-sam-tool/REPORT.md at PILOT scale:

  E2-volume : mode1_seg3d_volume  vs  mode1_no_vision   (nifti samples)
  E1-mesh   : mode1_seg3d_mesh     vs  mode1_no_vision   (mesh samples)

The seg modes advertise ONLY {execute_code, finish, sv3d_segment_3d_*} —
they do NOT advertise an open-scene tool, so the agent cannot mint a
session_id on its own. This driver therefore pre-opens a session via
`sv3d_open_scene` and injects the session_id into the agent's first user
turn. The baseline mode (mode1_no_vision) gets the same question but no
session (it has no segment tool to use it).

Real Anthropic API is used (one agent loop per sample). The fake SAM
server must be running at SAM_SERVICE_URL; its FAKE_SAM_QUALITY tier is
recorded per-run by the caller (passed via --tier, used only for labeling).

Usage:
    python -m scripts.sam_service.run_ablation_pilot \\
        --experiment e2_volume --tier noisy --n 20 \\
        --model claude-sonnet-4-6 --out evaluation_runs/ablation/e2_noisy.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from sci3d_agent.mcp import server  # noqa: F401  registers tools
from sci3d_agent.mcp.registry import REGISTRY
from sci3d_agent.agent import run_agent
from sci3d_agent.modes import get_profile
from sci3d_agent.kernel import FakeKernel
from sci3d_agent.viewer_bridge import StubViewerBridge
from sci3d_agent.llm_client import build_client

# experiment -> (seg_mode, baseline_mode, matched_file, asset_kind)
EXPERIMENTS = {
    "e2_volume": ("mode1_seg3d_volume", "mode1_no_vision",
                  "datasets/_matched/m3d-refseg.json", "nifti"),
    "e1_mesh": ("mode1_seg3d_mesh", "mode1_no_vision",
                "datasets/_matched/sqa3d.json", "mesh"),
}


def _flatten_pred(pred: Any) -> str:
    """The agent may finish with a string OR a structured dict. Pull the
    human-readable text out of a dict (label/summary/answer fields) so
    open-QA matching sees the actual claim, not JSON punctuation."""
    if isinstance(pred, dict):
        parts = []
        for key in ("answer", "label", "summary", "text", "description"):
            v = pred.get(key)
            if isinstance(v, str):
                parts.append(v)
        return " ".join(parts) if parts else json.dumps(pred)
    if isinstance(pred, list):
        return " ".join(_flatten_pred(x) for x in pred)
    return str(pred or "")


def _norm(text: Any) -> str:
    """Normalize an answer for lenient open-QA matching."""
    s = _flatten_pred(text).lower().strip()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# gold answers often carry boilerplate ("sure, a [X] can be seen in the [SEG]")
_GOLD_STOP = {"sure", "the", "a", "an", "can", "be", "seen", "in", "is",
              "this", "there", "of", "and", "to", "seg", "yes", "no", "it"}


def _gold_keywords(gold: Any) -> list[str]:
    return [w for w in _norm(gold).split() if w not in _GOLD_STOP and len(w) > 2]


def _is_correct(pred: Any, gold: Any) -> bool:
    """Lenient correctness for open-ended 3D QA.

    Empty predictions are ALWAYS wrong (an agent that ran out of steps
    without finishing must not score). Otherwise: exact normalized match,
    full-gold substring, or — for boilerplate-heavy gold — all content
    keywords present in the prediction."""
    p = _norm(pred)
    g = _norm(gold)
    if not p or not g:
        return False
    if p == g or g in p:
        return True
    kws = _gold_keywords(gold)
    if kws and all(k in p for k in kws):
        return True
    return False


def _load_records(matched_file: str, asset_kind: str, n: int) -> list[dict]:
    data = json.loads((REPO / matched_file).read_text())
    recs = [r for r in data["records"] if r.get("asset_kind") == asset_kind]
    out = []
    for r in recs:
        p = REPO / r["path_repo"]
        if p.exists():
            out.append(r)
        if len(out) >= n:
            break
    return out


def _open_session(asset_path: str) -> dict:
    """Pre-open a session via the MCP open-scene handler."""
    return REGISTRY["sv3d_open_scene"].handler({"asset_path": asset_path})


def _seg_tool_for(kind: str) -> str:
    return "sv3d_segment_3d_volume" if kind == "nifti" else "sv3d_segment_3d_mesh"


def _extract_seg_scores(traj_dict: dict) -> list[float]:
    """Pull SAM scores out of any segment tool results in the trajectory."""
    scores: list[float] = []
    for step in traj_dict.get("steps", []):
        res = step.get("result") or {}
        for key in ("masks", "segments"):
            for item in (res.get(key) or []):
                if isinstance(item, dict) and "score" in item:
                    scores.append(float(item["score"]))
    return scores


def _run_one(rec: dict, mode: str, model: str, kind: str, max_iters: int) -> dict:
    asset_path = rec["path_repo"]
    question = rec["question"]
    gold = rec["answer"]

    profile = get_profile(mode)
    uses_seg = any(t["name"].startswith("sv3d_segment") for t in profile.advertised_tools)

    session_id = None
    open_err = None
    if uses_seg:
        opened = _open_session(asset_path)
        if "error" in opened:
            open_err = opened["error"]
        else:
            session_id = opened["session_id"]

    if uses_seg and session_id:
        seg_tool = _seg_tool_for(kind)
        payload_text = (
            f"Question: {question}\n"
            f"Asset: {asset_path}\n"
            f"An open 3D session is available with session_id={session_id!r}. "
            f"Use the `{seg_tool}` tool with that session_id and a text_prompt "
            f"describing the object of interest to obtain its 3D location "
            f"(centroid, bbox, count) before answering. Then call finish."
        )
    else:
        payload_text = f"Question: {question}\nAsset: {asset_path}"

    payload = [{"role": "user", "content": payload_text}]
    t0 = time.time()
    run_error = None
    try:
        traj = run_agent(
            profile=profile, initial_payload=payload, llm=build_client(),
            kernel=FakeKernel(), bridge=StubViewerBridge(),
            task_id=rec["qid"], model=model, seed=0,
            asset_path=asset_path, max_iters=max_iters,
        )
        td = asdict(traj)
    except Exception as exc:  # API edge cases (e.g. parallel tool_use) must
        # not abort the whole experiment — record the sample as failed.
        run_error = f"{type(exc).__name__}: {exc}"[:300]
        td = {"final_answer": None, "steps": [], "token_counts": {}}
    seg_scores = _extract_seg_scores(td)
    return {
        "qid": rec["qid"],
        "mode": mode,
        "question": question,
        "gold": gold,
        "pred": td.get("final_answer"),
        "correct": _is_correct(td.get("final_answer"), gold),
        "n_steps": len(td.get("steps", [])),
        "seg_scores": seg_scores,
        "tokens": td.get("token_counts", {}),
        "open_error": open_err,
        "run_error": run_error,
        "latency_s": round(time.time() - t0, 1),
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--experiment", required=True, choices=list(EXPERIMENTS))
    p.add_argument("--tier", required=True, help="fake SAM tier label (perfect/noisy/wrong)")
    p.add_argument("--n", type=int, default=20)
    p.add_argument("--model", default="claude-sonnet-4-6")
    p.add_argument("--max-iters", type=int, default=12)
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)

    seg_mode, base_mode, matched_file, kind = EXPERIMENTS[args.experiment]
    records = _load_records(matched_file, kind, args.n)
    print(f"[{args.experiment}] tier={args.tier} kind={kind} "
          f"samples={len(records)} seg_mode={seg_mode} base={base_mode}", flush=True)

    results = {"experiment": args.experiment, "tier": args.tier, "model": args.model,
               "kind": kind, "seg_mode": seg_mode, "baseline_mode": base_mode,
               "n_samples": len(records), "runs": []}

    for i, rec in enumerate(records):
        for mode in (seg_mode, base_mode):
            r = _run_one(rec, mode, args.model, kind, args.max_iters)
            results["runs"].append(r)
            tag = "SEG " if mode == seg_mode else "BASE"
            sc = f" score={r['seg_scores']}" if r["seg_scores"] else ""
            print(f"  [{i+1}/{len(records)}] {tag} correct={r['correct']} "
                  f"pred={_norm(r['pred'])[:30]!r} gold={_norm(r['gold'])[:20]!r}"
                  f"{sc} steps={r['n_steps']}", flush=True)

    # aggregate
    def acc(mode):
        rs = [x for x in results["runs"] if x["mode"] == mode]
        return round(sum(x["correct"] for x in rs) / max(len(rs), 1), 3), len(rs)
    seg_acc, seg_n = acc(seg_mode)
    base_acc, base_n = acc(base_mode)
    results["summary"] = {
        "seg_acc": seg_acc, "seg_n": seg_n,
        "base_acc": base_acc, "base_n": base_n,
        "delta": round(seg_acc - base_acc, 3),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(results, indent=2, default=str))
    print(f"[done] seg_acc={seg_acc} base_acc={base_acc} "
          f"delta={results['summary']['delta']} -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
