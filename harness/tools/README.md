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

# Optional fail-closed source revision check (full SHA required):
python tools/sync_from_sciencevision.py \
  --source /path/to/Science-Vision \
  --expected-source-commit 0123456789abcdef0123456789abcdef01234567 \
  --dry-run
```

### Behavior

- Every glob in `.mirror-include` is evaluated relative to `--source`.
- `--expected-source-commit` requires `--source` to be the Git worktree root
  at exactly the supplied 40-character commit with no staged, tracked, or
  ordinary untracked changes. Ignored build outputs may exist when they are
  covered by the prune rules, but every manifest-matched file left in the copy
  plan must be tracked by the expected commit; ignored `.env` files therefore
  fail closed. Without the option, plain local directories remain supported;
  for a pinned non-worktree source, materialize the exact tree with
  `git archive` and verify its content separately.
- Files matching any glob are copied into this repo at the same relative
  path.
- Files already in this repo that fall under a mirrored path prefix and
  match the manifest but are missing from the source are deleted (so the
  mirror stays in sync as the source repo evolves).
- Runtime and generated directories are always pruned on both sides of the
  plan: `.git/`, `.qagen/`, `__pycache__/`, `.venv/`, `.pytest_cache/`,
  `.ruff_cache/`, `.mypy_cache/`, `build/`, `dist/`, `node_modules/`, and any
  directory ending in `.egg-info`; generated `.pyc`, `.pyo`, and `.coverage`
  files are pruned as well. They are neither copied from ScienceVision nor
  deleted from the local mirror.

### After a sync

Review the diff (`git status`, `git diff --stat`), then commit. There is
no automation — this script is meant to be run by hand.
