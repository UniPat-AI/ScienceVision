# Mirror Sync + Medical-Spatial Stage-1 Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a local `tools/sync_from_sciencevision.py` script that mirrors the agreed subset of `SueMarsR/Science-Vision` into this repo, run it to produce the first mirror batch, then land a runnable offline medical-spatial demo (`examples/02_medical_spatial_gt/`) with rewritten `quickstart.md`.

**Architecture:** Two PRs land in sequence. PR-1 introduces an authoritative `.mirror-include` manifest in this repo plus a Python sync script (TDD'd, local-only — no GitHub Action yet), then commits the first mirror batch. PR-2 adds an offline kidney-CT demo with executable ground truth (label-argmax over a NIfTI mask), and rewrites the quickstart's 6 TODO steps into runnable commands tagged as "stage 1 (offline)".

**Tech Stack:**
- Python ≥3.10 (already required by `pyproject.toml`)
- `nibabel` + `numpy` for NIfTI scoring (new `demo` extras)
- `pytest` for sync-script tests (already in `dev` extras)
- Bash for `data/fetch.sh`

## Global Constraints

- Python floor: `>=3.10` (from `pyproject.toml` line 10) — never use 3.11+ syntax.
- Files ≤450 lines in mirrored `sci3d_agent/` material; ≤800 lines elsewhere (from `docs/platform/architecture.md` line 23).
- Commit messages follow conventional format: `feat:` / `fix:` / `chore:` / `docs:` / `test:` / `refactor:`. No `Co-Authored-By:` trailer (attribution disabled globally per `~/.claude/settings.json`).
- Immutable patterns: no in-place mutation of inputs (per `~/.claude/rules/common/coding-style.md`). Return new objects.
- `.gitignore` policy: raw data binaries stay out of git. Adding new `examples/*/data/*.nii.gz` to ignore list rather than committing.
- Source layout decision: mirror **with original paths** — `QA-Gen/scripts/**` lands at `QA-Gen/scripts/**`, not rewritten to `scripts/qa-gen/**`. Documents that reference the old path must be updated.
- Mirror source: `../ScienceVision` (sibling checkout). Override via `--source` flag or `SCIENCEVISION_DIR` env var.
- Sync script is local-only for now (option B); no GitHub Action work in scope.

---

## File Structure

### PR-1 (Sync Infrastructure)

| File | Action | Responsibility |
|---|---|---|
| `.mirror-include` | CREATE | Authoritative glob manifest (one pattern per line, `#` comments). Source of truth consumed by the sync script. |
| `MIRROR.md` | MODIFY | Rewritten as a human-readable summary that points at `.mirror-include`; corrects the `scripts/qa-gen/**` → `QA-Gen/**` mismatch. |
| `tools/__init__.py` | CREATE | Empty — marks `tools/` as a Python package so the sync module is importable from tests. |
| `tools/sync_from_sciencevision.py` | CREATE | Sync logic + CLI: parse manifest, enumerate matching source files, compute copy/delete plan, apply or dry-run. ~120 LOC. |
| `tools/README.md` | CREATE | One-page "what's in `tools/` and how to run the sync". |
| `tests/__init__.py` | CREATE | Empty — marks tests as a package. |
| `tests/test_sync_from_sciencevision.py` | CREATE | Pytest tests for `parse_manifest`, `matches_any`, `compute_plan`, exercising a tmp-dir fixture. |
| `docs/platform/architecture.md` | MODIFY | Line 12: replace `scripts/qa-gen/` with `QA-Gen/`. |
| `docs/platform/new-domain-template.md` | MODIFY | Lines referring to `scripts/qa-gen/<domain>/` → `QA-Gen/tasks/<domain>/`. |
| _Many mirrored files_ | CREATE (via script) | Output of first mirror run: `sci3d_agent/**`, `viewer/**`, `scripts/match/**`, `scripts/sam_service/**`, `QA-Gen/scripts/**`, `QA-Gen/tasks/MedicalKAxisPilot/**`, `datasets/_matched/*.json`, selected `docs/*.md`, `tests/mcp/**`. Committed as a single "mirror batch 0" commit. |

### PR-2 (Medical-Spatial Demo + Quickstart Rewrite)

| File | Action | Responsibility |
|---|---|---|
| `pyproject.toml` | MODIFY | Add `demo = ["nibabel>=5.0"]` optional-extra. |
| `.gitignore` | MODIFY | Add `examples/*/data/*.nii.gz` + `examples/*/data/*.npy` to ignored set. |
| `examples/02_medical_spatial_gt/README.md` | MODIFY | Replaces placeholder. Documents what the demo shows, lists prereqs, points to quickstart. |
| `examples/02_medical_spatial_gt/qa.json` | CREATE | One QA item with executable-ground-truth recipe (no hardcoded answer). |
| `examples/02_medical_spatial_gt/data/.gitkeep` | CREATE | Keeps the otherwise-empty `data/` dir in git. |
| `examples/02_medical_spatial_gt/data/fetch.sh` | CREATE | Pulls the kidney-CT sample from the sibling ScienceVision checkout (or `SCIENCEVISION_DIR`). |
| `examples/02_medical_spatial_gt/score.py` | CREATE | Offline scorer: argmax over mask label voxel counts, compare to `\boxed{…}` answer. ~50 LOC. |
| `tests/test_medical_score.py` | CREATE | Pytest for `extract_answer` and `ground_truth` (synthetic mask). |
| `docs/platform/quickstart.md` | MODIFY | Rewrites the 6 TODO steps into runnable commands for the stage-1 offline path. |

---

## Task Decomposition

### PR-1 — Sync Infrastructure (Tasks 1–8)

---

### Task 1: Author `.mirror-include` manifest and rewrite `MIRROR.md`

**Files:**
- Create: `.mirror-include`
- Modify: `MIRROR.md` (full rewrite)

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `.mirror-include` glob list consumed by `tools/sync_from_sciencevision.py` in Task 4; the corrected `QA-Gen/`-rooted layout consumed by docs touch-ups in Task 7.

- [ ] **Step 1: Create `.mirror-include` with the corrected manifest**

Write `/Users/sue/Research/BabyVision-v2/.mirror-include`:

```
# .mirror-include — authoritative manifest consumed by
# tools/sync_from_sciencevision.py. Each non-empty, non-comment line is
# a glob evaluated relative to the ScienceVision source repo root.
# `**` matches recursively; `*` matches a single path segment.

# --- Code: agent + viewer + supporting scripts ---
sci3d_agent/**
viewer/**
scripts/match/**
scripts/sam_service/**
tools/setup_worktree_data_links.sh
tests/mcp/**

# --- QA-gen pipeline (lives at repo root in ScienceVision) ---
QA-Gen/scripts/**
QA-Gen/tasks/MedicalKAxisPilot/**

# --- 3D-grounded whitelist indices (metadata only, no raw assets) ---
datasets/_matched/*.json

# --- Subset of ScienceVision documentation ---
docs/CrawlDataAndViewer.md
docs/DataSourcesAndFormats.md
docs/mcp/MCP_SERVER_SPEC.md
docs/mcp/MCP_ROLLOUT_PLAN.md
docs/qa-gen/**
docs/evaluation/**
docs/Tools.md
docs/3d_assets_inventory.md
```

- [ ] **Step 2: Rewrite `MIRROR.md` to reflect reality**

Replace the file contents with:

```markdown
# Mirror manifest (informational summary)

This file is a human-readable summary of [`.mirror-include`](.mirror-include),
which is the **authoritative** list of paths mirrored from
[`SueMarsR/Science-Vision`](https://github.com/SueMarsR/Science-Vision) into this
repository. The sync script that consumes it lives at
[`tools/sync_from_sciencevision.py`](tools/sync_from_sciencevision.py).

## Running a sync locally

```bash
# Dry-run: show what would change without touching disk.
python tools/sync_from_sciencevision.py --source ../ScienceVision --dry-run

# Apply: copies matching files into this repo at the same relative paths.
python tools/sync_from_sciencevision.py --source ../ScienceVision
```

Requires a sibling ScienceVision checkout at `../ScienceVision` (override with
`--source`). The script is **local-only** today — no GitHub Action yet.

## What's mirrored

The manifest is split into four groups; the comments inside `.mirror-include`
identify each one:

1. **Code** — `sci3d_agent/**`, `viewer/**`, `scripts/match/**`,
   `scripts/sam_service/**`, `tests/mcp/**`.
2. **QA-gen pipeline** — `QA-Gen/scripts/**` and the
   `QA-Gen/tasks/MedicalKAxisPilot/**` task batch. **Note:** ScienceVision
   keeps its QA-gen tree at `QA-Gen/` at the repo root (not under `scripts/`).
   The mirror preserves that layout verbatim, so paths line up 1-to-1 with
   the source repo.
3. **3D-grounded whitelist indices** — `datasets/_matched/*.json`.
   Metadata-only; raw datasets are pulled separately by partners.
4. **Documentation subset** — `docs/CrawlDataAndViewer.md`,
   `docs/DataSourcesAndFormats.md`, `docs/mcp/*.md`, `docs/qa-gen/**`,
   `docs/evaluation/**`, `docs/Tools.md`, `docs/3d_assets_inventory.md`.

## What's NOT mirrored

- Raw datasets (`datasets/<name>/raw/`, `datasets/<name>/derived/`,
  `crawl_data/`, `_dl_pool/`, `out_samples/`) — partners pull separately.
- Internal-only docs (deep-research, meeting notes, stage reviews, project
  plans, related-work surveys, screenshots).
- `CLAUDE.md`, `TODO.md`, `.claire/**`, `.claude/**`.

For the source-side reasoning behind each exclusion, see the comments in
the ScienceVision repo. If the manifest and this summary ever disagree,
the manifest wins.
```

- [ ] **Step 3: Verify the manifest parses cleanly (smoke test)**

Run:
```bash
cd /Users/sue/Research/BabyVision-v2
grep -vE '^\s*(#|$)' .mirror-include | wc -l
```
Expected: a positive integer (count of active globs), no errors.

- [ ] **Step 4: Commit**

```bash
git checkout -b feat/mirror-infra
git add .mirror-include MIRROR.md
git commit -m "feat: add authoritative .mirror-include manifest, fix MIRROR.md"
```

---

### Task 2: TDD — write failing tests for `parse_manifest` and `matches_any`

**Files:**
- Create: `tests/__init__.py` (empty)
- Create: `tests/test_sync_from_sciencevision.py`
- Create: `tools/__init__.py` (empty)

**Interfaces:**
- Consumes: nothing yet (functions don't exist).
- Produces: test contract pinning `parse_manifest(path: Path) -> list[str]` and `matches_any(rel: Path, patterns: list[str]) -> bool` signatures, consumed by Task 3 and Task 4 implementations.

- [ ] **Step 1: Create empty package markers**

```bash
mkdir -p /Users/sue/Research/BabyVision-v2/tools /Users/sue/Research/BabyVision-v2/tests
touch /Users/sue/Research/BabyVision-v2/tools/__init__.py /Users/sue/Research/BabyVision-v2/tests/__init__.py
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_sync_from_sciencevision.py`:

```python
"""Tests for tools.sync_from_sciencevision."""
from __future__ import annotations

import pathlib

import pytest

from tools import sync_from_sciencevision as sync


def test_parse_manifest_strips_comments_and_blank_lines(tmp_path: pathlib.Path) -> None:
    manifest = tmp_path / ".mirror-include"
    manifest.write_text(
        "# a comment\n"
        "\n"
        "sci3d_agent/**\n"
        "viewer/**   # trailing comment\n"
        "\n"
        "  scripts/match/**  \n"
    )
    patterns = sync.parse_manifest(manifest)
    assert patterns == ["sci3d_agent/**", "viewer/**", "scripts/match/**"]


def test_matches_any_recursive_glob() -> None:
    patterns = ["sci3d_agent/**", "datasets/_matched/*.json"]
    assert sync.matches_any(pathlib.Path("sci3d_agent/mcp/server.py"), patterns)
    assert sync.matches_any(pathlib.Path("sci3d_agent/x.py"), patterns)
    assert sync.matches_any(pathlib.Path("datasets/_matched/scanqa.json"), patterns)
    assert not sync.matches_any(pathlib.Path("datasets/_matched/sub/scanqa.json"), patterns)
    assert not sync.matches_any(pathlib.Path("README.md"), patterns)


def test_matches_any_single_file_pattern() -> None:
    patterns = ["tools/setup_worktree_data_links.sh"]
    assert sync.matches_any(pathlib.Path("tools/setup_worktree_data_links.sh"), patterns)
    assert not sync.matches_any(pathlib.Path("tools/other.sh"), patterns)
```

- [ ] **Step 3: Run the tests and verify they fail**

```bash
cd /Users/sue/Research/BabyVision-v2
pip install -e ".[dev]" >/dev/null
python -m pytest tests/test_sync_from_sciencevision.py -v
```
Expected: ImportError / ModuleNotFoundError on `from tools import sync_from_sciencevision`. (Failure is the goal here.)

- [ ] **Step 4: Commit**

```bash
git add tests/__init__.py tests/test_sync_from_sciencevision.py tools/__init__.py
git commit -m "test: pin sync_from_sciencevision parse_manifest + matches_any contracts"
```

---

### Task 3: TDD — write failing tests for `compute_plan`

**Files:**
- Modify: `tests/test_sync_from_sciencevision.py` (append two more tests)

**Interfaces:**
- Consumes: `parse_manifest`, `matches_any` test contracts from Task 2.
- Produces: contract for `compute_plan(source: Path, dest: Path, patterns: list[str]) -> Plan` where `Plan` is a frozen dataclass with `copies: list[Path]` and `deletes: list[Path]`; consumed by Task 4.

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_sync_from_sciencevision.py`:

```python


def test_compute_plan_copies_matched_files_and_deletes_stale(tmp_path: pathlib.Path) -> None:
    source = tmp_path / "src"
    dest = tmp_path / "dst"
    (source / "sci3d_agent").mkdir(parents=True)
    (source / "sci3d_agent" / "a.py").write_text("a")
    (source / "sci3d_agent" / "sub").mkdir()
    (source / "sci3d_agent" / "sub" / "b.py").write_text("b")
    (source / "other").mkdir()
    (source / "other" / "ignored.py").write_text("ignored")

    (dest / "sci3d_agent").mkdir(parents=True)
    (dest / "sci3d_agent" / "stale.py").write_text("stale")

    plan = sync.compute_plan(source, dest, ["sci3d_agent/**"])

    assert sorted(p.as_posix() for p in plan.copies) == [
        "sci3d_agent/a.py",
        "sci3d_agent/sub/b.py",
    ]
    assert [p.as_posix() for p in plan.deletes] == ["sci3d_agent/stale.py"]


def test_compute_plan_ignores_dot_dirs(tmp_path: pathlib.Path) -> None:
    source = tmp_path / "src"
    dest = tmp_path / "dst"
    (source / "sci3d_agent" / ".git").mkdir(parents=True)
    (source / "sci3d_agent" / ".git" / "config").write_text("git-stuff")
    (source / "sci3d_agent" / "__pycache__").mkdir()
    (source / "sci3d_agent" / "__pycache__" / "x.pyc").write_bytes(b"")
    (source / "sci3d_agent" / "real.py").write_text("real")

    plan = sync.compute_plan(source, dest, ["sci3d_agent/**"])

    assert [p.as_posix() for p in plan.copies] == ["sci3d_agent/real.py"]
```

- [ ] **Step 2: Run and verify failure**

```bash
python -m pytest tests/test_sync_from_sciencevision.py -v
```
Expected: still ImportError on `tools.sync_from_sciencevision` (module not written yet).

- [ ] **Step 3: Commit**

```bash
git add tests/test_sync_from_sciencevision.py
git commit -m "test: pin sync compute_plan contract (copies, deletes, dot-dir pruning)"
```

---

### Task 4: Implement `tools/sync_from_sciencevision.py` to pass all tests

**Files:**
- Create: `tools/sync_from_sciencevision.py`

**Interfaces:**
- Consumes: test contracts from Tasks 2 and 3.
- Produces: `parse_manifest`, `matches_any`, `compute_plan`, `apply_plan`, `Plan` dataclass, and a CLI `main()` consumed by Tasks 5–6.

- [ ] **Step 1: Write the implementation**

Create `tools/sync_from_sciencevision.py`:

```python
"""Mirror selected files from a sibling ScienceVision checkout into this repo.

Reads `.mirror-include` (one glob per line, `#` for comments). Each glob is
evaluated relative to the source repo root. Matching files are copied to
this repo at the same relative path. Files already in this repo that fall
under any mirrored path-prefix but are no longer matched are deleted.

CLI:
    python tools/sync_from_sciencevision.py --source ../ScienceVision --dry-run
    python tools/sync_from_sciencevision.py --source ../ScienceVision
"""
from __future__ import annotations

import argparse
import dataclasses
import fnmatch
import os
import pathlib
import shutil
import sys
from typing import Iterable

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = (REPO_ROOT.parent / "ScienceVision").resolve()
DEFAULT_MANIFEST = REPO_ROOT / ".mirror-include"

_PRUNE_DIRS = frozenset(
    {".git", "__pycache__", ".venv", ".pytest_cache",
     ".ruff_cache", ".mypy_cache", "node_modules"}
)


@dataclasses.dataclass(frozen=True)
class Plan:
    copies: list[pathlib.Path]
    deletes: list[pathlib.Path]


def parse_manifest(manifest: pathlib.Path) -> list[str]:
    patterns: list[str] = []
    for raw in manifest.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            patterns.append(line)
    return patterns


def _pattern_prefix(pattern: str) -> str:
    """Longest fixed-string directory prefix of a glob."""
    cut = len(pattern)
    for i, ch in enumerate(pattern):
        if ch in "*?[":
            cut = i
            break
    head = pattern[:cut]
    return head[: head.rfind("/") + 1]


def matches_any(rel: pathlib.Path, patterns: list[str]) -> bool:
    rel_str = rel.as_posix()
    for pat in patterns:
        if pat.endswith("/**"):
            prefix = pat[:-3] + "/"
            if rel_str.startswith(prefix):
                return True
            continue
        if fnmatch.fnmatchcase(rel_str, pat):
            return True
    return False


def _iter_source(source: pathlib.Path, patterns: list[str]) -> Iterable[pathlib.Path]:
    for root, dirs, files in os.walk(source):
        dirs[:] = [d for d in dirs if d not in _PRUNE_DIRS]
        root_path = pathlib.Path(root)
        for fn in files:
            rel = (root_path / fn).relative_to(source)
            if matches_any(rel, patterns):
                yield rel


def _iter_dest_under_prefixes(
    dest: pathlib.Path, prefixes: Iterable[str]
) -> Iterable[pathlib.Path]:
    seen: set[pathlib.Path] = set()
    for prefix in prefixes:
        if not prefix:
            continue
        base = dest / prefix
        if not base.exists():
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in _PRUNE_DIRS]
            for fn in files:
                rel = (pathlib.Path(root) / fn).relative_to(dest)
                if rel not in seen:
                    seen.add(rel)
                    yield rel


def compute_plan(
    source: pathlib.Path, dest: pathlib.Path, patterns: list[str]
) -> Plan:
    source_files = sorted(_iter_source(source, patterns))
    keep = set(source_files)
    prefixes = {_pattern_prefix(p) for p in patterns}
    dest_existing = set(_iter_dest_under_prefixes(dest, prefixes))
    # Only consider deletes that the patterns would have included if they
    # existed in source — i.e. things under a mirrored prefix that match
    # some pattern.
    delete_candidates = {p for p in dest_existing if matches_any(p, patterns)}
    deletes = sorted(delete_candidates - keep)
    return Plan(copies=source_files, deletes=deletes)


def apply_plan(plan: Plan, source: pathlib.Path, dest: pathlib.Path) -> None:
    for rel in plan.copies:
        src = source / rel
        dst = dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    for rel in plan.deletes:
        target = dest / rel
        if target.exists():
            target.unlink()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", type=pathlib.Path, default=DEFAULT_SOURCE,
                    help="Path to ScienceVision checkout (default: ../ScienceVision)")
    ap.add_argument("--manifest", type=pathlib.Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--dest", type=pathlib.Path, default=REPO_ROOT)
    ap.add_argument("--dry-run", action="store_true",
                    help="Print actions without touching disk.")
    args = ap.parse_args(argv)

    if not args.source.exists():
        print(f"error: source not found: {args.source}", file=sys.stderr)
        return 2
    if not args.manifest.exists():
        print(f"error: manifest not found: {args.manifest}", file=sys.stderr)
        return 2

    patterns = parse_manifest(args.manifest)
    plan = compute_plan(args.source.resolve(), args.dest.resolve(), patterns)
    print(f"plan: {len(plan.copies)} copies, {len(plan.deletes)} deletes")
    if args.dry_run:
        for rel in plan.copies[:50]:
            print(f"  + {rel}")
        if len(plan.copies) > 50:
            print(f"  ... and {len(plan.copies) - 50} more copies")
        for rel in plan.deletes:
            print(f"  - {rel}")
        return 0

    apply_plan(plan, args.source.resolve(), args.dest.resolve())
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run all sync tests; verify they pass**

```bash
cd /Users/sue/Research/BabyVision-v2
python -m pytest tests/test_sync_from_sciencevision.py -v
```
Expected: 5 passed.

- [ ] **Step 3: Smoke-test the CLI with `--dry-run` against the sibling checkout**

```bash
python tools/sync_from_sciencevision.py --source ../ScienceVision --dry-run
```
Expected: prints `plan: <N> copies, 0 deletes` (deletes are 0 because nothing has been mirrored yet) where N is in the low thousands. No errors.

- [ ] **Step 4: Commit**

```bash
git add tools/sync_from_sciencevision.py
git commit -m "feat(tools): add local sync script for ScienceVision mirror"
```

---

### Task 5: Add `tools/README.md`

**Files:**
- Create: `tools/README.md`

**Interfaces:**
- Consumes: the CLI surface defined in Task 4.
- Produces: nothing programmatic — humans only.

- [ ] **Step 1: Write the README**

Create `tools/README.md`:

```markdown
# `tools/`

Repo-local utilities. Not shipped to users.

## `sync_from_sciencevision.py`

Mirrors selected files from a sibling [`SueMarsR/Science-Vision`](https://github.com/SueMarsR/Science-Vision)
checkout into this repo. Reads the authoritative glob manifest at
[`.mirror-include`](../.mirror-include).

### Usage

```bash
# Show what would change without touching disk.
python tools/sync_from_sciencevision.py --source ../ScienceVision --dry-run

# Apply the mirror.
python tools/sync_from_sciencevision.py --source ../ScienceVision

# Custom source path:
python tools/sync_from_sciencevision.py --source /path/to/Science-Vision
```

### Behavior

- Every glob in `.mirror-include` is evaluated relative to `--source`.
- Files matching any glob are copied into this repo at the same relative
  path.
- Files already in this repo that fall under a mirrored path prefix and
  match the manifest but are missing from the source are deleted (so the
  mirror stays in sync as the source repo evolves).
- `.git/`, `__pycache__/`, `.venv/`, `.pytest_cache/`, `.ruff_cache/`,
  `.mypy_cache/`, `node_modules/` are always pruned.

### After a sync

Review the diff (`git status`, `git diff --stat`), then commit. There is
no automation — this script is meant to be run by hand.
```

- [ ] **Step 2: Commit**

```bash
git add tools/README.md
git commit -m "docs(tools): document sync_from_sciencevision usage"
```

---

### Task 6: Run the first real mirror and commit the batch

**Files:**
- Create: many — output of the script.

**Interfaces:**
- Consumes: working sync script from Task 4 + manifest from Task 1.
- Produces: source tree of mirrored files (`sci3d_agent/**`, `viewer/**`, `scripts/match/**`, `scripts/sam_service/**`, `tests/mcp/**`, `QA-Gen/scripts/**`, `QA-Gen/tasks/MedicalKAxisPilot/**`, `datasets/_matched/*.json`, selected docs) consumed by PR-2 and downstream documentation.

- [ ] **Step 1: Dry-run, eyeball the plan**

```bash
cd /Users/sue/Research/BabyVision-v2
python tools/sync_from_sciencevision.py --source ../ScienceVision --dry-run | tee /tmp/sync-dryrun.txt
```
Expected: `plan: <N> copies, 0 deletes` with N in the low thousands. Sanity-check the `+` lines: should see `sci3d_agent/`, `viewer/`, `QA-Gen/scripts/`, `QA-Gen/tasks/MedicalKAxisPilot/`, `datasets/_matched/*.json`, and the docs subset. Should NOT see `crawl_data/`, `out_samples/`, `_dl_pool/`, `.claude/`, `CLAUDE.md`, `TODO.md`.

- [ ] **Step 2: Apply for real**

```bash
python tools/sync_from_sciencevision.py --source ../ScienceVision
```
Expected: `plan: <N> copies, 0 deletes` followed by `done`.

- [ ] **Step 3: Inspect the diff size**

```bash
git status --short | wc -l        # number of changed paths
du -sh sci3d_agent viewer QA-Gen datasets/_matched 2>/dev/null
git diff --stat --cached 2>/dev/null | tail -5
```
Expected: file count matches dry-run; disk size is well under 200 MB; no `crawl_data/` or `out_samples/` showed up.

- [ ] **Step 4: Spot-check three key files exist and look right**

```bash
test -f sci3d_agent/mcp/server.py && echo OK1 || echo MISSING1
test -f QA-Gen/tasks/MedicalKAxisPilot/ScienceVision_MedicalKAxisPilot_final_tasks.jsonl && echo OK2 || echo MISSING2
test -f datasets/_matched/m3d-seg-0005.json && echo OK3 || echo MISSING3
```
Expected: three `OK` lines.

- [ ] **Step 5: Stage and commit the batch**

```bash
git add -A
git commit -m "chore(mirror): batch 0 — initial mirror from ScienceVision"
```

If `git add -A` would also stage unrelated untracked files (e.g. `docs/TODO.md` shown in the initial `git status`), stage explicitly per-directory instead:
```bash
git add sci3d_agent viewer scripts QA-Gen datasets/_matched tests/mcp docs
git commit -m "chore(mirror): batch 0 — initial mirror from ScienceVision"
```

---

### Task 7: Update the two stale `scripts/qa-gen` references in docs

**Files:**
- Modify: `docs/platform/architecture.md`
- Modify: `docs/platform/new-domain-template.md`

**Interfaces:**
- Consumes: the now-correct `QA-Gen/` layout produced by Task 6.
- Produces: docs that match reality, consumed by PR-2's quickstart rewrite.

- [ ] **Step 1: Fix `architecture.md` line 12**

Replace:
```markdown
- **QA-gen pipeline** (`scripts/qa-gen/`): deterministic at runtime; LLM cost is build-time only.
```
With:
```markdown
- **QA-gen pipeline** (`QA-Gen/`): deterministic at runtime; LLM cost is build-time only. `QA-Gen/scripts/` holds shared utilities; `QA-Gen/tasks/<batch>/` holds each task batch (e.g. `MedicalKAxisPilot/`, `Genesis_VP_Hard/`).
```

- [ ] **Step 2: Fix `new-domain-template.md`**

Open the file and find every reference to `scripts/qa-gen/`. There are at least two:

In section "## 2. `_matched/` whitelist registration" → keep `build_matched_3d_qa.py` reference but update path if needed (it lives at `scripts/match/build_matched_3d_qa.py` — unchanged, so no edit here).

In section "## 4. QA pipeline invocation":

Replace:
```markdown
- [ ] Define your task batch (P0/P1/P2) under `scripts/qa-gen/<your_domain>/`
```
With:
```markdown
- [ ] Define your task batch (P0/P1/P2) under `QA-Gen/tasks/<your_domain>/`
```

- [ ] **Step 3: Verify there are no other stale references**

```bash
grep -rn 'scripts/qa-gen' docs/ examples/ README.md MIRROR.md
```
Expected: no output. If anything turns up, edit it to `QA-Gen/`.

- [ ] **Step 4: Commit**

```bash
git add docs/platform/architecture.md docs/platform/new-domain-template.md
git commit -m "docs: update QA-gen path references to QA-Gen/ (mirror reality)"
```

---

### Task 8: Open PR-1

**Files:** none — git/gh only.

**Interfaces:**
- Consumes: branch `feat/mirror-infra` with Tasks 1–7 committed.
- Produces: an open PR.

- [ ] **Step 1: Push and open**

```bash
cd /Users/sue/Research/BabyVision-v2
git push -u origin feat/mirror-infra
gh pr create --base main --head feat/mirror-infra \
  --title "feat: mirror infrastructure + first batch from ScienceVision" \
  --body "$(cat <<'EOF'
## Summary
- Add `.mirror-include` as the authoritative manifest of paths mirrored from `SueMarsR/Science-Vision`
- Add `tools/sync_from_sciencevision.py` — local-only sync script (no GitHub Action yet)
- Run the script once → commit the first mirror batch (`sci3d_agent/`, `viewer/`, `scripts/match/`, `scripts/sam_service/`, `QA-Gen/scripts/`, `QA-Gen/tasks/MedicalKAxisPilot/`, `datasets/_matched/*.json`, selected docs)
- Fix `MIRROR.md` to reflect actual ScienceVision layout (`QA-Gen/` at root, not `scripts/qa-gen/`)
- Fix two stale `scripts/qa-gen` references in `docs/platform/`

## Why local-only and not a GitHub Action?
We don't yet know which files MUST be in the mirror to unblock real partner work. The local script lets us iterate cheaply on `.mirror-include` and re-run. Once the manifest stabilizes, the same logic can be lifted into an Action.

## Test plan
- [ ] `python -m pytest tests/test_sync_from_sciencevision.py -v` — 5/5 pass
- [ ] `python tools/sync_from_sciencevision.py --source ../ScienceVision --dry-run` runs without error
- [ ] `grep -rn 'scripts/qa-gen' docs/ examples/ README.md MIRROR.md` returns no matches
- [ ] Spot-check `sci3d_agent/mcp/server.py`, `QA-Gen/tasks/MedicalKAxisPilot/ScienceVision_MedicalKAxisPilot_final_tasks.jsonl`, `datasets/_matched/m3d-seg-0005.json` are all present
EOF
)"
```

- [ ] **Step 2: Verify**

```bash
gh pr view --web
```
Land it (self-merge or wait for review per project norms).

---

### PR-2 — Medical-Spatial Stage-1 Demo (Tasks 9–15)

**Branch from `main` AFTER PR-1 is merged** so the demo can reference mirrored files (e.g. `datasets/_matched/m3d-seg-0005.json`).

```bash
git checkout main && git pull
git checkout -b feat/medical-spatial-stage1
```

---

### Task 9: Add `demo` optional-extras and update `.gitignore`

**Files:**
- Modify: `pyproject.toml`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: nothing.
- Produces: `pip install -e ".[demo]"` provides `nibabel`, consumed by Task 11.

- [ ] **Step 1: Add `demo` extras**

Open `pyproject.toml`. After the `eval` block (lines 40–45), insert a new `demo` block:

```toml
demo = [
    "nibabel>=5.0",
    "numpy>=1.26",
]
```

`numpy>=1.26` is already a hard dependency, but pinning it inside `demo` makes the extras self-contained for partners who use `pip install babyvision-v2[demo]` without `pip install -e ".[all]"`.

- [ ] **Step 2: Update `.gitignore`**

Open `.gitignore`. After the existing `# Mirrored data` block (around line 27–33), append:

```gitignore

# Example demo data (partners fetch via examples/*/data/fetch.sh)
examples/*/data/*.nii.gz
examples/*/data/*.npy
```

- [ ] **Step 3: Verify install picks up the new extras**

```bash
cd /Users/sue/Research/BabyVision-v2
pip install -e ".[demo]"
python -c "import nibabel, numpy; print(nibabel.__version__, numpy.__version__)"
```
Expected: prints two version strings, no errors.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml .gitignore
git commit -m "feat(pyproject): add [demo] extras (nibabel) + ignore example data binaries"
```

---

### Task 10: TDD — write failing tests for `extract_answer` and `ground_truth`

**Files:**
- Create: `tests/test_medical_score.py`

**Interfaces:**
- Consumes: nothing yet.
- Produces: contract for `extract_answer(text: str) -> str | None` (regex extracts `\boxed{X}`) and `ground_truth(mask_path: Path) -> str` (label-argmax over labels 1–4), consumed by Task 11.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_medical_score.py`:

```python
"""Tests for the offline medical-spatial scorer."""
from __future__ import annotations

import pathlib
import sys

import nibabel as nib
import numpy as np
import pytest

# The scorer is a script next to the demo data, not a package — import via path.
EXAMPLE_DIR = (
    pathlib.Path(__file__).resolve().parent.parent
    / "examples" / "02_medical_spatial_gt"
)
sys.path.insert(0, str(EXAMPLE_DIR))
import score  # noqa: E402


def test_extract_answer_finds_boxed_token() -> None:
    assert score.extract_answer(r"my answer is \boxed{Kidney}.") == "Kidney"
    assert score.extract_answer(r"  \boxed{Renal vein}  ") == "Renal vein"


def test_extract_answer_returns_none_when_missing() -> None:
    assert score.extract_answer("Kidney") is None
    assert score.extract_answer("") is None


def test_ground_truth_picks_largest_label(tmp_path: pathlib.Path) -> None:
    # 10x10x10 volume: label 2 occupies a 6x6x6 block (216 voxels),
    # labels 1, 3, 4 each get a single voxel.
    arr = np.zeros((10, 10, 10), dtype=np.int16)
    arr[:6, :6, :6] = 2
    arr[0, 0, 9] = 1
    arr[0, 9, 0] = 3
    arr[9, 0, 0] = 4
    nii = nib.Nifti1Image(arr, affine=np.eye(4))
    mask_path = tmp_path / "mask.nii.gz"
    nib.save(nii, str(mask_path))

    assert score.ground_truth(mask_path) == "Kidney"
```

- [ ] **Step 2: Run and verify failure**

```bash
python -m pytest tests/test_medical_score.py -v
```
Expected: `ModuleNotFoundError: No module named 'score'` (because the file does not exist yet) or `FileNotFoundError` on the import path.

- [ ] **Step 3: Commit**

```bash
git add tests/test_medical_score.py
git commit -m "test: pin medical scorer contract (extract_answer, ground_truth)"
```

---

### Task 11: Implement `examples/02_medical_spatial_gt/score.py`

**Files:**
- Create: `examples/02_medical_spatial_gt/score.py`

**Interfaces:**
- Consumes: test contracts from Task 10.
- Produces: a runnable `python score.py --prediction <file>` CLI consumed by the rewritten `quickstart.md` (Task 14).

- [ ] **Step 1: Write the implementation**

Create `examples/02_medical_spatial_gt/score.py`:

```python
"""Stage-1 offline scorer for the medical-spatial demo.

Reads the predicted answer from --prediction (a text file containing a
`\\boxed{<class name>}` token), computes the ground truth from
data/mask.nii.gz (label with the largest voxel count, mapped to a class
name), and emits a JSON verdict. Exit 0 on match, 1 on mismatch, 2 on a
setup error (missing prediction file or missing mask).

Usage:
    python score.py --prediction my_answer.txt
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import nibabel as nib
import numpy as np

LABEL_NAMES: dict[int, str] = {
    1: "Renal vein",
    2: "Kidney",
    3: "Renal artery",
    4: "Tumor",
}

_BOXED_RE = re.compile(r"\\boxed\{([^}]+)\}")


def extract_answer(text: str) -> str | None:
    """Return the contents of the first `\\boxed{...}` token, stripped."""
    match = _BOXED_RE.search(text)
    return match.group(1).strip() if match else None


def ground_truth(mask_path: pathlib.Path) -> str:
    """Return the LABEL_NAMES entry with the largest voxel count in the mask."""
    arr = np.asarray(nib.load(str(mask_path)).dataobj, dtype=np.int64)
    counts = {label: int((arr == label).sum()) for label in LABEL_NAMES}
    largest_label = max(counts, key=lambda lbl: counts[lbl])
    return LABEL_NAMES[largest_label]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--prediction", type=pathlib.Path, required=True,
                    help="Text file containing the model's answer.")
    ap.add_argument("--mask", type=pathlib.Path,
                    default=pathlib.Path(__file__).resolve().parent / "data" / "mask.nii.gz",
                    help="NIfTI mask used to compute ground truth.")
    args = ap.parse_args(argv)

    if not args.prediction.exists():
        print(f"error: prediction file not found: {args.prediction}", file=sys.stderr)
        return 2
    if not args.mask.exists():
        print(
            f"error: mask not found: {args.mask}\n"
            "       Did you run `bash data/fetch.sh`?",
            file=sys.stderr,
        )
        return 2

    expected = ground_truth(args.mask)
    predicted = extract_answer(args.prediction.read_text())
    verdict = {
        "expected": expected,
        "predicted": predicted,
        "match": predicted == expected,
    }
    print(json.dumps(verdict, indent=2))
    return 0 if verdict["match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run the scorer tests and verify they pass**

```bash
cd /Users/sue/Research/BabyVision-v2
python -m pytest tests/test_medical_score.py -v
```
Expected: 3 passed.

- [ ] **Step 3: Commit**

```bash
git add examples/02_medical_spatial_gt/score.py
git commit -m "feat(examples): offline scorer for medical-spatial demo"
```

---

### Task 12: Add `data/fetch.sh`, `data/.gitkeep`, and `qa.json`

**Files:**
- Create: `examples/02_medical_spatial_gt/data/.gitkeep`
- Create: `examples/02_medical_spatial_gt/data/fetch.sh`
- Create: `examples/02_medical_spatial_gt/qa.json`

**Interfaces:**
- Consumes: `mask.nii.gz` location convention from Task 11.
- Produces: a fetchable sample + a single QA item, consumed by Task 13 README and Task 14 quickstart.

- [ ] **Step 1: Create the data dir placeholder**

```bash
mkdir -p /Users/sue/Research/BabyVision-v2/examples/02_medical_spatial_gt/data
touch /Users/sue/Research/BabyVision-v2/examples/02_medical_spatial_gt/data/.gitkeep
```

- [ ] **Step 2: Write `fetch.sh`**

Create `examples/02_medical_spatial_gt/data/fetch.sh`:

```bash
#!/usr/bin/env bash
# Fetch the m3d-seg-0005 case-0 NIfTI volume + mask used by the demo.
#
# Stage 1: requires a sibling ScienceVision checkout. The public
# HuggingFace mirror lands in stage 1.5; until then, set
# SCIENCEVISION_DIR if your checkout lives elsewhere.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${SCIENCEVISION_DIR:-$HERE/../../../../ScienceVision}"
CASE_DIR="$SOURCE_DIR/datasets/_derived/m3d-seg-0005/0"

if [[ ! -f "$CASE_DIR/image.nii.gz" || ! -f "$CASE_DIR/mask.nii.gz" ]]; then
    echo "error: source files not found under $CASE_DIR" >&2
    echo "       Either clone SueMarsR/Science-Vision next to this repo," >&2
    echo "       or set SCIENCEVISION_DIR=/path/to/your/checkout." >&2
    exit 2
fi

echo "[fetch] copying from $CASE_DIR"
cp "$CASE_DIR/image.nii.gz" "$HERE/image.nii.gz"
cp "$CASE_DIR/mask.nii.gz"  "$HERE/mask.nii.gz"
ls -lh "$HERE"/*.nii.gz
echo "[fetch] done"
```

```bash
chmod +x /Users/sue/Research/BabyVision-v2/examples/02_medical_spatial_gt/data/fetch.sh
```

- [ ] **Step 3: Write `qa.json`**

Create `examples/02_medical_spatial_gt/qa.json`:

```json
{
  "qid": "demo-m3d-seg-0005-0-largest-class",
  "task_family": "Medical_Spatial",
  "asset": "data/image.nii.gz",
  "asset_mask": "data/mask.nii.gz",
  "question": "This kidney CT volume comes with a 4-class segmentation mask: 1=Renal vein, 2=Kidney, 3=Renal artery, 4=Tumor. Which class occupies the LARGEST number of voxels? Answer in the form \\boxed{<class name>}.",
  "ground_truth_recipe": "argmax over per-label voxel counts of data/mask.nii.gz, restricted to labels {1: 'Renal vein', 2: 'Kidney', 3: 'Renal artery', 4: 'Tumor'}.",
  "metric": "exact_match",
  "expected_answer": null
}
```

`expected_answer: null` is intentional — the answer is **computed from the mask** at scoring time, never hardcoded. This is the BabyVision v1 "executable ground truth" pattern.

- [ ] **Step 4: Smoke-test the fetch + score loop**

```bash
cd /Users/sue/Research/BabyVision-v2/examples/02_medical_spatial_gt
bash data/fetch.sh
echo '\boxed{Kidney}' > /tmp/my_answer.txt
python score.py --prediction /tmp/my_answer.txt
echo "exit: $?"
```
Expected:
```json
{
  "expected": "Kidney",
  "predicted": "Kidney",
  "match": true
}
```
followed by `exit: 0`.

- [ ] **Step 5: Commit**

```bash
cd /Users/sue/Research/BabyVision-v2
git add examples/02_medical_spatial_gt/data/.gitkeep \
        examples/02_medical_spatial_gt/data/fetch.sh \
        examples/02_medical_spatial_gt/qa.json
git commit -m "feat(examples): add fetch script and QA item for medical-spatial demo"
```

---

### Task 13: Rewrite `examples/02_medical_spatial_gt/README.md`

**Files:**
- Modify: `examples/02_medical_spatial_gt/README.md` (full rewrite)

**Interfaces:**
- Consumes: the demo skeleton from Tasks 11–12.
- Produces: human-readable entry doc, consumed by the quickstart rewrite in Task 14.

- [ ] **Step 1: Overwrite the placeholder**

Replace the contents of `examples/02_medical_spatial_gt/README.md` with:

```markdown
# `examples/02_medical_spatial_gt/`

End-to-end **stage-1 (offline)** demo for the Medical Spatial task family:
one kidney-CT volume with a 4-class segmentation mask, one question, one
offline scorer that derives ground truth from the mask itself.

> Stage 1 deliberately skips the live MCP server + viewer loop. It exists
> so partners can reproduce the "executable ground truth" shape of a
> BabyVision-v2 task in under 10 minutes on a laptop. Stage 2 will add
> the live `sci3d_agent` MCP server and the URL-parameterized viewer.

## Contents

| File | Purpose |
|---|---|
| `qa.json` | The single QA item (question + ground-truth recipe + metric). |
| `data/fetch.sh` | Pulls `image.nii.gz` (~6 MB) and `mask.nii.gz` (~100 KB) from the sibling ScienceVision checkout. |
| `data/image.nii.gz` | CT volume — fetched by `fetch.sh`, NOT in git. |
| `data/mask.nii.gz` | 4-class mask — fetched by `fetch.sh`, NOT in git. |
| `score.py` | Reads the mask, computes the answer, compares to your prediction. |

## Running it

See [`docs/platform/quickstart.md`](../../docs/platform/quickstart.md) for
the full 6-step walkthrough. The short version:

```bash
pip install -e ".[demo]"
cd examples/02_medical_spatial_gt
bash data/fetch.sh
echo '\boxed{Kidney}' > my_answer.txt
python score.py --prediction my_answer.txt
```

Expected output:

```json
{
  "expected": "Kidney",
  "predicted": "Kidney",
  "match": true
}
```

## Why this design

- **Executable ground truth.** The answer is recomputed from `mask.nii.gz`
  at scoring time, never hardcoded. This is BabyVision v1's first line of
  defense against LLM-as-judge (see [`docs/benchmark/evaluation-protocol.md`](../../docs/benchmark/evaluation-protocol.md)).
- **Stage-1 honesty.** No MCP server, no viewer, no tool trace — those
  are stage 2. Stage 1 demonstrates *only* the offline-scoring loop.
- **Self-contained.** No external API keys, no GPU, no internet (with a
  sibling ScienceVision checkout).

## Stage 2 (not yet runnable)

- `sci3d_agent.mcp.server` — 56-tool MCP server (already mirrored at
  `sci3d_agent/`, but the partner-facing launch story isn't wired here yet).
- Viewer pages under `viewer/` rendered via the
  `sv3d_build_<kind>_url` MCP tools.
- A trace-fidelity score on top of the answer-match score.
```

- [ ] **Step 2: Commit**

```bash
git add examples/02_medical_spatial_gt/README.md
git commit -m "docs(examples): rewrite medical-spatial README for stage-1 demo"
```

---

### Task 14: Rewrite `docs/platform/quickstart.md`

**Files:**
- Modify: `docs/platform/quickstart.md` (full rewrite of the 6-TODO section)

**Interfaces:**
- Consumes: the runnable demo from Tasks 11–13.
- Produces: the partner-facing landing doc for the stage-1 demo.

- [ ] **Step 1: Overwrite the quickstart**

Replace the contents of `docs/platform/quickstart.md` with:

```markdown
# Quickstart

> **Goal: in ≤30 minutes, run a 3D-grounded QA demo end-to-end on your
> machine — no UniPat engineer in the loop.**

> Status: **stage 1 (offline)**. The full end-to-end loop with the live
> MCP server, URL-parameterized viewer, and runtime QA generation is
> stage 2. Stage 1 exercises the offline-scoring shape so you can
> validate the "executable ground truth" pattern before wiring the rest.

---

## Pick a demo

| Demo | When to pick | Path |
|---|---|---|
| **Medical Spatial (offline)** ✓ stage 1 ready | Any partner. CT volume + 4-class mask + 1 QA + offline scorer. ~5 min on a laptop. | [`examples/02_medical_spatial_gt/`](../../examples/02_medical_spatial_gt/) |
| **Genesis_VP_Hard** (4 physics tasks) — stage 2 | Embodied / physical reasoning background; force, MPM, FEM, cloth. | [`examples/01_genesis_vp_quickstart/`](../../examples/01_genesis_vp_quickstart/) (placeholder, not yet runnable) |

The rest of this page walks the Medical Spatial demo top-to-bottom.

---

## Step 1 — install

```bash
git clone https://github.com/UniPat-AI/BabyVision-v2.git
cd BabyVision-v2
pip install -e ".[demo]"
```

Requires Python ≥3.10 and ~10 MB free disk for the sample.

## Step 2 — fetch the demo sample

```bash
cd examples/02_medical_spatial_gt
bash data/fetch.sh
```

This expects a sibling [`SueMarsR/Science-Vision`](https://github.com/SueMarsR/Science-Vision)
checkout at `../../../../ScienceVision` (i.e. next to your `BabyVision-v2`
clone). Override with:

```bash
SCIENCEVISION_DIR=/path/to/ScienceVision bash data/fetch.sh
```

Expected: `data/image.nii.gz` (~6 MB) and `data/mask.nii.gz` (~100 KB).

> Stage 1.5 will publish these two files to a public HuggingFace dataset
> so the sibling-checkout step goes away. Tracked at the top of `data/fetch.sh`.

## Step 3 — inspect the volume (optional)

Open `data/image.nii.gz` in any NIfTI viewer (ITK-SNAP, 3D Slicer, FSL,
`niivue`). The mask labels are:

| Label | Structure |
|---|---|
| 1 | Renal vein |
| 2 | Kidney |
| 3 | Renal artery |
| 4 | Tumor |

This step is *recommended* but not required — the scorer doesn't depend
on it.

## Step 4 — write your answer

The single demo question is in [`qa.json`](../../examples/02_medical_spatial_gt/qa.json):

> "This kidney CT volume comes with a 4-class segmentation mask
> (1=Renal vein, 2=Kidney, 3=Renal artery, 4=Tumor). Which class occupies
> the LARGEST number of voxels? Answer in the form `\boxed{<class name>}`."

Write your answer to a text file:

```bash
echo '\boxed{Kidney}' > my_answer.txt
```

## Step 5 — run the offline scorer

```bash
python score.py --prediction my_answer.txt
```

Expected output:

```json
{
  "expected": "Kidney",
  "predicted": "Kidney",
  "match": true
}
```

Exit code is `0` on match, `1` on mismatch, `2` on setup errors (missing
prediction file or missing mask). This makes it CI-friendly:

```bash
python score.py --prediction my_answer.txt > /dev/null && echo PASS || echo FAIL
```

## Step 6 — what's next

- **Read the eval protocol** — [`docs/benchmark/evaluation-protocol.md`](../benchmark/evaluation-protocol.md)
  explains the layered defense against LLM-as-judge that this offline
  scorer is one example of.
- **Submit to the leaderboard** — once you've run your model against the
  Medical Spatial task family, package the result per
  [`docs/benchmark/submission-guide.md`](../benchmark/submission-guide.md)
  and open a PR.
- **Add your own domain** — follow [`new-domain-template.md`](new-domain-template.md).
- **Stage 2 (live MCP + viewer)** is in progress; track in
  [`examples/01_genesis_vp_quickstart/`](../../examples/01_genesis_vp_quickstart/).

---

## Troubleshooting

- **`fetch.sh` says source files not found** — your ScienceVision checkout
  isn't at `../../../../ScienceVision`. Either clone it there, or set
  `SCIENCEVISION_DIR=/path/to/ScienceVision`.
- **`ModuleNotFoundError: No module named 'nibabel'`** — you ran the
  scorer without the `demo` extras. `pip install -e ".[demo]"` first.
- **`predicted: null`** — your `my_answer.txt` must contain the literal
  `\boxed{...}` token, not just plain text. The scorer extracts via
  regex.
- **Match is false but the answer is right** — case- and whitespace-
  sensitive. `\boxed{kidney}` ≠ `\boxed{Kidney}`. Match the spelling in
  the `LABEL_NAMES` table above exactly.
```

- [ ] **Step 2: Verify there are no stale TODO checkboxes**

```bash
grep -n '^- \[ \]' docs/platform/quickstart.md
```
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add docs/platform/quickstart.md
git commit -m "docs(platform): rewrite quickstart as runnable stage-1 medical demo"
```

---

### Task 15: End-to-end smoke run + open PR-2

**Files:** none — git/gh only.

**Interfaces:**
- Consumes: branch `feat/medical-spatial-stage1` with Tasks 9–14 committed.
- Produces: an open PR.

- [ ] **Step 1: Cold-start the demo from scratch**

```bash
cd /tmp && rm -rf bv2-smoke
git clone /Users/sue/Research/BabyVision-v2 bv2-smoke
cd bv2-smoke
git checkout feat/medical-spatial-stage1
pip install -e ".[demo]" >/dev/null
cd examples/02_medical_spatial_gt
SCIENCEVISION_DIR=/Users/sue/Research/ScienceVision bash data/fetch.sh
echo '\boxed{Kidney}' > my_answer.txt
python score.py --prediction my_answer.txt
echo "exit: $?"
```
Expected: `"match": true`, `exit: 0`.

- [ ] **Step 2: Also confirm the mismatch path returns exit 1**

```bash
echo '\boxed{Tumor}' > wrong_answer.txt
python score.py --prediction wrong_answer.txt; echo "exit: $?"
```
Expected: `"match": false`, `exit: 1`.

- [ ] **Step 3: Run all tests at the repo root**

```bash
cd /tmp/bv2-smoke
python -m pytest tests/ -v
```
Expected: 8 passed (5 sync + 3 scorer). No failures.

- [ ] **Step 4: Clean up the smoke clone**

```bash
rm -rf /tmp/bv2-smoke
```

- [ ] **Step 5: Push and open PR-2**

```bash
cd /Users/sue/Research/BabyVision-v2
git push -u origin feat/medical-spatial-stage1
gh pr create --base main --head feat/medical-spatial-stage1 \
  --title "feat: medical-spatial stage-1 offline demo + quickstart rewrite" \
  --body "$(cat <<'EOF'
## Summary
- Add the first runnable demo in `examples/02_medical_spatial_gt/`: one CT volume, one mask, one QA, one offline scorer with executable ground truth (no hardcoded answer)
- Add `[demo]` extras (`nibabel`) and ignore example data binaries
- Rewrite `docs/platform/quickstart.md`'s 6 TODO steps as runnable commands
- 3 new scorer tests pinning `extract_answer` + `ground_truth` behavior

## Stage 1 vs stage 2
**Stage 1** (this PR) exercises only the offline-scoring shape. No MCP server, no viewer, no trace. That's enough to validate the "executable ground truth defends against LLM-as-judge" pattern on a partner's laptop in ~5 minutes.

**Stage 2** (next) wires the mirrored `sci3d_agent/` MCP server and `viewer/` into the same demo. Tracked in `examples/01_genesis_vp_quickstart/` README.

## Test plan
- [ ] `python -m pytest tests/ -v` — 8/8 pass (5 sync from PR-1, 3 scorer new)
- [ ] Cold-clone, install with `[demo]`, fetch, score with `\\boxed{Kidney}` → exit 0
- [ ] Same cold-clone, score with `\\boxed{Tumor}` → exit 1, `match: false`
- [ ] `grep -n '^- \[ \]' docs/platform/quickstart.md` returns no output (no stale TODOs)
EOF
)"
```

- [ ] **Step 6: Verify**

```bash
gh pr view --web
```

---

## Self-Review

**1. Spec coverage check:**

- ✓ B (local sync script): Tasks 1–8, with `tools/sync_from_sciencevision.py` as the script, `.mirror-include` as its manifest, and an explicit "no GitHub Action yet" note in the PR description.
- ✓ Z (stage-1 offline demo first, defer X): Tasks 9–15 deliver an offline-only Medical Spatial demo. README and quickstart both label it "stage 1"; stage 2 (live MCP + viewer) is explicitly out of scope and pointed to from the docs.
- ✓ Fix MIRROR.md mismatch: Task 1 rewrites `MIRROR.md` and creates `.mirror-include` with the corrected `QA-Gen/`-rooted paths. Task 7 also fixes the two stale `scripts/qa-gen` references in `docs/platform/`.
- ✓ Two-PR split: PR-1 is sync infrastructure + first mirror; PR-2 is the demo + quickstart. PR-2 is branched after PR-1 is merged.

**2. Placeholder scan:** searched for "TBD", "TODO", "fill in", "later", "appropriate", "similar to" — none in the plan body. Every code block contains full code, every command shows expected output.

**3. Type consistency:**
- `parse_manifest(path) -> list[str]` — Tasks 2, 4 — consistent.
- `matches_any(rel: Path, patterns: list[str]) -> bool` — Tasks 2, 4 — consistent.
- `compute_plan(source, dest, patterns) -> Plan` with `Plan(copies, deletes)` — Tasks 3, 4 — consistent.
- `extract_answer(text) -> str | None` — Tasks 10, 11 — consistent.
- `ground_truth(mask_path) -> str` — Tasks 10, 11 — consistent.
- `LABEL_NAMES` dict mapping {1: "Renal vein", 2: "Kidney", 3: "Renal artery", 4: "Tumor"} — Tasks 10, 11, 12, 13, 14 — consistent.
- `\boxed{<name>}` format — Tasks 10, 11, 12 (qa.json question), 14 (quickstart Step 4) — consistent.
- Exit codes 0 / 1 / 2 — Task 11 (impl) and Task 14 (quickstart documents) — consistent.

No issues found; plan is ready.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-22-mirror-and-medical-demo.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.

**Which approach?**
