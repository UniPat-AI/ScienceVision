#!/usr/bin/env python3
"""Offline entry point for the pinned deterministic ScienceVision scorer.

The command accepts either a Harbor ``task.toml``/task JSON or the plain
``[metadata]`` equivalent, plus an answer JSON/text file. It intentionally
does not import the agent, an MCP client/server, or an LLM registry.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from manifest import canonical_json, sha256_bytes
from offline import disable_network

# Enable the in-process guard before any task-controlled scorer imports.
disable_network()


RESULT_SCHEMA_VERSION = "sciencevision.scorer_result.v1"


@dataclass(frozen=True)
class _AnswerView:
    final_answer: Any


class InputError(ValueError):
    """A malformed input that prevents an auditable scoring decision."""


def _read_json_or_text(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InputError(f"cannot_read:{path}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # A terminal response may be strict JSON *text* rather than a JSON
        # wrapper. The deterministic parser gets to reject prose/fenced JSON.
        return text


def _read_task(path: Path) -> tuple[dict[str, Any], str, str]:
    """Read a metadata document and return ``(metadata, id, instruction)``."""
    suffix = path.suffix.casefold()
    if suffix == ".toml":
        try:
            import tomllib

            payload: Any = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise InputError(f"invalid_toml_task:{path}") from exc
    else:
        payload = _read_json_or_text(path)
    if not isinstance(payload, Mapping):
        raise InputError("task_document_must_be_an_object")

    raw_metadata = payload.get("metadata")
    metadata = dict(raw_metadata) if isinstance(raw_metadata, Mapping) else dict(payload)
    task_table = payload.get("task")
    task_table = task_table if isinstance(task_table, Mapping) else {}
    task_id = str(
        metadata.get("task_id")
        or payload.get("task_id")
        or task_table.get("name")
        or payload.get("id")
        or "unknown-task"
    )
    instruction = str(
        payload.get("instruction")
        or task_table.get("instruction")
        or metadata.get("instruction")
        or metadata.get("question")
        or ""
    )
    return metadata, task_id, instruction


def _read_answer(path: Path) -> Any:
    value = _read_json_or_text(path)
    if isinstance(value, Mapping) and set(value) == {"answer"}:
        return value["answer"]
    return value


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InputError(f"invalid_or_missing_manifest:{path}") from exc
    if not isinstance(value, dict):
        raise InputError("manifest_must_be_an_object")
    claimed = value.pop("manifest_sha256", None)
    calculated = sha256_bytes(canonical_json(value))
    if not isinstance(claimed, str) or claimed != calculated:
        raise InputError("manifest_integrity_error")
    value["manifest_sha256"] = claimed
    if value.get("schema_version") != "sciencevision.scorer_bundle_manifest.v1":
        raise InputError("unsupported_manifest_schema")
    return value


def _blocked(
    manifest: Mapping[str, Any],
    *,
    task_id: str,
    primary: str,
    reason_codes: list[str],
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    audit: dict[str, Any] = {
        "evaluation_eligible": False,
        "reason_codes": reason_codes,
        "network_access": "not_used",
    }
    if extra:
        audit.update(extra)
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "status": "blocked",
        "task_id": task_id,
        "scorer_contract": primary,
        "scorer_manifest_sha256": manifest["manifest_sha256"],
        "score": None,
        "audit": audit,
    }


def score_one(
    metadata: dict[str, Any],
    answer: Any,
    *,
    task_id: str,
    instruction: str,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Score one formal contract or return an explicit blocked result.

    A missing flag is not treated as permission: ``evaluation_eligible`` must
    be literally ``true`` in the immutable task metadata.
    """
    scorer = metadata.get("scorer")
    scorer = scorer if isinstance(scorer, Mapping) else {}
    primary = str(scorer.get("primary") or "")
    if metadata.get("evaluation_eligible") is not True:
        code = (
            "task_marked_ineligible"
            if metadata.get("evaluation_eligible") is False
            else "missing_explicit_evaluation_eligible"
        )
        return _blocked(
            manifest,
            task_id=task_id,
            primary=primary,
            reason_codes=[code],
            extra={"evaluation_ready": metadata.get("evaluation_ready")},
        )

    try:
        from harbor_adapters.sci3d._verify_translate import task_metadata_to_taskspec
        from sci3d_bench.scorers.deterministic_answer import (
            DeterministicAnswerScorer,
            SCORER_ALIASES,
        )
        from sci3d_bench.scorers.extent_voxel_tolerance import (
            ExtentVoxelToleranceScorer,
        )
    except ModuleNotFoundError as exc:  # image construction/integrity failure
        return _blocked(
            manifest,
            task_id=task_id,
            primary=primary,
            reason_codes=["incomplete_scorer_bundle"],
            extra={"detail": exc.name},
        )

    if primary in {*SCORER_ALIASES, "exact_match", "tolerance_match", "expert_rubric"}:
        scorer_impl: Any = DeterministicAnswerScorer()
    elif primary == ExtentVoxelToleranceScorer.name:
        scorer_impl = ExtentVoxelToleranceScorer()
    else:
        # In particular, LLM/prose judges and mask-Dice are deliberately not
        # smuggled into this image: they require an external model or private
        # artifact and therefore cannot be a reproducible formal contract.
        return _blocked(
            manifest,
            task_id=task_id,
            primary=primary,
            reason_codes=["unsupported_or_non_deterministic_scorer"],
        )

    try:
        task = task_metadata_to_taskspec(
            metadata, task_id=task_id, instruction=instruction
        )
        score = scorer_impl.score(task, _AnswerView(final_answer=answer))
    except Exception as exc:  # malformed immutable contract; do not infer it
        return _blocked(
            manifest,
            task_id=task_id,
            primary=primary,
            reason_codes=["task_contract_error"],
            extra={"error_type": type(exc).__name__},
        )

    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "status": "scored",
        "task_id": task_id,
        "scorer_contract": primary,
        "scorer_manifest_sha256": manifest["manifest_sha256"],
        "score": {
            "value": float(score.value),
            "metric": score.metric,
            "degraded": bool(score.degraded),
        },
        "audit": {
            "evaluation_eligible": True,
            "network_access": "not_used",
            "raw": score.raw,
        },
    }


def _write_result(value: Mapping[str, Any], output: str) -> None:
    encoded = canonical_json(value) + b"\n"
    if output == "-":
        sys.stdout.buffer.write(encoded)
        return
    Path(output).write_bytes(encoded)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, type=Path, help="task.toml or JSON metadata")
    parser.add_argument("--answer", required=True, type=Path, help="answer JSON or terminal text")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).with_name("scorer_manifest.json"),
        help="pinned scorer manifest (default: adjacent to entry point)",
    )
    parser.add_argument("--output", default="-", help="result JSON path, or - for stdout")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = _load_manifest(args.manifest)
        metadata, task_id, instruction = _read_task(args.task)
        answer = _read_answer(args.answer)
        result = score_one(
            metadata,
            answer,
            task_id=task_id,
            instruction=instruction,
            manifest=manifest,
        )
    except InputError as exc:
        result = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "status": "input_error",
            "score": None,
            "audit": {"reason_codes": [str(exc)], "network_access": "not_used"},
        }
        _write_result(result, args.output)
        return 2
    _write_result(result, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
