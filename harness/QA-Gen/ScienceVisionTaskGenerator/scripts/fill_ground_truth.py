#!/usr/bin/env python3
"""Standalone CLI: fill ground_truth.value on a QA-Gen *_final_tasks.jsonl.

Usage:
    python -m scripts.fill_ground_truth --in <input.jsonl> --out <output.jsonl> \
        [--no-llm] [--model claude-sonnet-4-6]

Behaviour:
    - With --no-llm (or no credentials), only NotImplementedFiller runs.
      Every task with a registered concrete filler stays unchanged
      (status: needs_expert_annotation -> awaiting_expert).
    - Without --no-llm, the following fillers are wired with a real
      VisionChatClient targeting the Mindra OpenAI-compat gateway:
        * RenderedVisibilityFiller (V2): rendered_visibility_label
        * CameraImageAnnotationFiller (V3): camera_plus_image_annotation

Exits 0 on success.  Prints the StageReport to stderr.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from sciencevision_taskgen.llm.client import ChatClientError

logger = logging.getLogger("fill_ground_truth")


def _resolve_repo_root() -> Path:
    # scripts/<here>.py -> ScienceVisionTaskGenerator -> QA-Gen -> repo root
    return Path(__file__).resolve().parents[3]


def _wire_concrete_fillers(repo_root: Path, model: str) -> None:
    from sciencevision_taskgen.groundtruth._camera_image_annotation import (
        register_default as register_camera_default,
    )
    from sciencevision_taskgen.groundtruth._rendered_visibility import (
        register_default as register_visibility_default,
    )
    from sciencevision_taskgen.llm._vision import VisionChatClient
    from sciencevision_taskgen.llm.routing import load_credentials

    base_url, api_key = load_credentials()
    client = VisionChatClient(base_url=base_url, api_key=api_key, timeout_sec=60.0)
    register_visibility_default(client=client, model=model, repo_root=repo_root)
    register_camera_default(client=client, model=model, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fill ground_truth on a QA-Gen jsonl.")
    parser.add_argument("--in", dest="in_path", required=True, type=Path)
    parser.add_argument("--out", dest="out_path", required=True, type=Path)
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip wiring concrete LLM-backed fillers; only NotImplementedFiller runs.",
    )
    parser.add_argument(
        "--model",
        default="claude-sonnet-4-6",
        help="Model id passed to all vision-LLM-backed fillers.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    repo_root = _resolve_repo_root()

    from sciencevision_taskgen.groundtruth import FILLERS  # noqa: F401
    from sciencevision_taskgen.pipeline.groundtruth_stage import GroundTruthStage

    if not args.no_llm:
        try:
            _wire_concrete_fillers(repo_root, args.model)
        except (ValueError, ChatClientError) as exc:
            logger.warning(
                "Could not wire LLM fillers (%s); proceeding with --no-llm path.",
                exc,
            )

    tasks_in = []
    for raw in args.in_path.read_text().splitlines():
        raw = raw.strip()
        if raw:
            tasks_in.append(json.loads(raw))

    tasks_out, report = GroundTruthStage(repo_root=repo_root).fill_tasks(tasks_in)

    args.out_path.parent.mkdir(parents=True, exist_ok=True)
    with args.out_path.open("w") as fh:
        for t in tasks_out:
            fh.write(json.dumps(t, ensure_ascii=False) + "\n")

    print(
        f"GroundTruthStage report:\n"
        f"  Total:                       {report.total}\n"
        f"  auto_seed (filled):          {report.auto_seed}\n"
        f"  awaiting_expert (unfilled):  {report.awaiting_expert}\n"
        f"  needs_expert_annotation:     {report.needs_expert_annotation}\n",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
