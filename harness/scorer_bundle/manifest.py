"""Deterministic manifest generation for the isolated scorer bundle.

This module deliberately uses only the Python standard library. The manifest
is both an inventory of the files copied into the release image and a compact
compatibility contract for the deterministic comparator registry.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Iterable


MANIFEST_SCHEMA_VERSION = "sciencevision.scorer_bundle_manifest.v1"

# These are the only repository sources that the build context is allowed to
# copy. Keeping the allow-list here makes an accidental agent/MCP dependency
# visible in review and in the resulting manifest.
BUNDLED_SOURCE_FILES = (
    "sci3d_bench/task_schema.py",
    "sci3d_bench/scorers/base.py",
    "sci3d_bench/scorers/deterministic_answer.py",
    "sci3d_bench/scorers/extent_voxel_tolerance.py",
    "harbor_adapters/sci3d/_tool_names.py",
    "harbor_adapters/sci3d/_tool_outcomes.py",
    "harbor_adapters/sci3d/_verify_translate.py",
)


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _git_revision(repo_root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"], text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _git_dirty(repo_root: Path) -> bool | None:
    """Check only bundle inputs, never the checkout's large LFS cache.

    The content hashes remain the authority. This flag is diagnostic only and
    is intentionally ``None`` rather than blocking a release when Git cannot
    answer within a small, bounded interval.
    """
    try:
        result = subprocess.run(
            [
                "git", "-C", str(repo_root), "status", "--porcelain=v1",
                "--untracked-files=all", "--", *BUNDLED_SOURCE_FILES,
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
        )
        return bool(result.stdout.strip()) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _source_segment(source: str, node: ast.AST) -> str:
    lines = source.splitlines(keepends=True)
    start = getattr(node, "lineno", 1) - 1
    end = getattr(node, "end_lineno", start + 1)
    return "".join(lines[start:end])


def comparator_registry(repo_root: Path) -> dict[str, dict[str, str]]:
    """Return a stable, implementation-sensitive comparator inventory."""
    source_path = repo_root / "sci3d_bench/scorers/deterministic_answer.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(source_path))
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    registry_node: ast.Assign | ast.AnnAssign | None = None
    for node in tree.body:
        targets: Iterable[ast.expr]
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = (node.target,)
        else:
            continue
        if any(
            isinstance(target, ast.Name) and target.id == "_COMPARATORS"
            for target in targets
        ):
            registry_node = node
            break
    if registry_node is None:
        raise ValueError("deterministic comparator registry not found")
    value = registry_node.value
    if not isinstance(value, ast.Dict):
        raise ValueError("deterministic comparator registry is not a dict")

    registry: dict[str, dict[str, str]] = {}
    for key, implementation in zip(value.keys, value.values):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            raise ValueError("comparator registry key is not a string")
        if not isinstance(implementation, ast.Name) or implementation.id not in functions:
            raise ValueError(f"comparator implementation missing: {key.value!r}")
        function = functions[implementation.id]
        registry[key.value] = {
            "function": implementation.id,
            "implementation_sha256": sha256_bytes(
                _source_segment(source, function).encode("utf-8")
            ),
        }
    return dict(sorted(registry.items()))


def build_manifest(
    repo_root: Path,
    *,
    bundle_version: str,
    source_date_epoch: int | None = None,
    runtime_files: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Construct a manifest without wall-clock state.

    ``source_date_epoch`` is optional and excluded by default so two builds of
    the same source tree produce byte-identical manifests.
    """
    repo_root = Path(repo_root).resolve()
    files: dict[str, str] = {}
    for relative in BUNDLED_SOURCE_FILES:
        candidate = repo_root / relative
        if not candidate.is_file():
            raise FileNotFoundError(f"required scorer source is absent: {relative}")
        files[relative] = sha256_file(candidate)

    registry = comparator_registry(repo_root)
    runtime = dict(sorted((runtime_files or {}).items()))
    payload: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "bundle_version": str(bundle_version).strip(),
        "git_revision": _git_revision(repo_root),
        "git_dirty": _git_dirty(repo_root),
        "source_tree_sha256": sha256_bytes(canonical_json(files)),
        "runtime_files": runtime,
        "bundle_tree_sha256": sha256_bytes(
            canonical_json({"source": files, "runtime": runtime})
        ),
        "schema_sha256": files["sci3d_bench/task_schema.py"],
        "comparator_registry": registry,
        "comparator_registry_sha256": sha256_bytes(canonical_json(registry)),
        "bundled_source_files": files,
        "formal_scorers": [
            "deterministic_structured_v2_1",
            "structured_deterministic_rubric_v1",
            "extent_voxel_tolerance_v1",
        ],
        "network_policy": "no_network_runtime_required",
    }
    if source_date_epoch is not None:
        payload["source_date_epoch"] = int(source_date_epoch)
    payload["manifest_sha256"] = sha256_bytes(canonical_json(payload))
    return payload


def write_manifest(
    output: Path,
    repo_root: Path,
    *,
    bundle_version: str,
    source_date_epoch: int | None = None,
    runtime_files: dict[str, str] | None = None,
) -> dict[str, Any]:
    manifest = build_manifest(
        repo_root,
        bundle_version=bundle_version,
        source_date_epoch=source_date_epoch,
        runtime_files=runtime_files,
    )
    Path(output).write_bytes(canonical_json(manifest) + b"\n")
    return manifest


def source_date_epoch_from_environment() -> int | None:
    value = os.environ.get("SOURCE_DATE_EPOCH")
    return int(value) if value else None
