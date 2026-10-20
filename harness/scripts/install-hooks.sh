#!/usr/bin/env bash
# Point git at the checked-in hook directory so contributors get the
# pre-commit guard without per-machine symlink work. Idempotent — safe
# to re-run.
set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

git config core.hooksPath scripts/git-hooks
chmod +x scripts/git-hooks/* 2>/dev/null || true

echo "Installed: core.hooksPath -> scripts/git-hooks"
echo "Current hooks:"
ls -1 scripts/git-hooks/
