#!/usr/bin/env python3
"""In-container scorer for the sci3d containerized Harbor route.

This script runs *inside* the verifier container (the image installs this repo,
so ``harbor_adapters.sci3d`` is importable). It is intentionally a thin shim:
all scoring logic lives in ``harbor_adapters.sci3d._score_core.score_rewards``,
the *same* function the host-run ``Sci3dVerifier`` calls. There is no second
copy of the scoring logic — host and container share one code path.

Responsibilities here (container-side glue only):
  1. Read the run inputs that Harbor mounts / passes:
       - agent artifacts under ``/logs/agent`` (sci3d_answer.json, trajectory)
       - the task's [metadata] table via the SCI3D_METADATA env var (a JSON
         blob the generated test.sh exports from task.toml), with task id +
         instruction from SCI3D_TASK_ID / SCI3D_INSTRUCTION.
  2. Call ``score_rewards`` to get ``(rewards, audit)``.
  3. Write the multi-key reward to ``/logs/verifier/reward.json`` (Harbor reads
     this) and the richer audit to ``/logs/verifier/sci3d_scores.json``.

Env knobs (set by test.sh / task.toml [verifier].env):
    SCI3D_METADATA      JSON of the [metadata] table        (required)
    SCI3D_TASK_ID       task id string                      (default: env task)
    SCI3D_INSTRUCTION   instruction text                    (default: "")
    SCI3D_JUDGE_MODEL   LLM-judge model id                  (optional)
    SCI3D_AGENT_DIR     override agent dir   (default: /logs/agent)
    SCI3D_VERIFIER_DIR  override out dir      (default: /logs/verifier)
    SCI3D_USE_FAKE_LLM  "1" -> offline fake judge (CI / no-network)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from harbor_adapters.sci3d._score_core import AUDIT_FILE, score_rewards

_DEFAULT_AGENT_DIR = "/logs/agent"
_DEFAULT_VERIFIER_DIR = "/logs/verifier"
_REWARD_FILE = "reward.json"


def _load_metadata() -> dict:
    blob = os.environ.get("SCI3D_METADATA", "").strip()
    if not blob:
        return {}
    try:
        meta = json.loads(blob)
    except json.JSONDecodeError as exc:
        print(f"sci3d score.py: SCI3D_METADATA is not valid JSON: {exc}", file=sys.stderr)
        return {}
    return meta if isinstance(meta, dict) else {}


def main() -> int:
    meta = _load_metadata()
    task_id = os.environ.get("SCI3D_TASK_ID", "harbor-task")
    instruction = os.environ.get("SCI3D_INSTRUCTION", "")
    judge_model = os.environ.get("SCI3D_JUDGE_MODEL") or None
    agent_dir = Path(os.environ.get("SCI3D_AGENT_DIR", _DEFAULT_AGENT_DIR))
    verifier_dir = Path(os.environ.get("SCI3D_VERIFIER_DIR", _DEFAULT_VERIFIER_DIR))

    rewards, audit = score_rewards(
        meta,
        task_id=task_id,
        instruction=instruction,
        agent_dir=agent_dir,
        judge_model=judge_model,
    )

    verifier_dir.mkdir(parents=True, exist_ok=True)
    # Harbor reads reward.json (multi-key dict of float). Keep audit beside it.
    (verifier_dir / _REWARD_FILE).write_text(
        json.dumps(rewards, ensure_ascii=False, indent=2)
    )
    (verifier_dir / AUDIT_FILE).write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, default=str)
    )
    print(
        f"sci3d score.py: wrote {len(rewards)} reward keys to "
        f"{verifier_dir / _REWARD_FILE} (reward={rewards.get('reward')})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
