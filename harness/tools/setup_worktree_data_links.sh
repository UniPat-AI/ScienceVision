#!/usr/bin/env bash
# Populate the heavy gitignored data trees in a Claude Code worktree by
# symlinking them from the main checkout.
#
# The viewer's static manifests reference /crawl_data/<domain>/<sub>/...
# and /out_samples/<dataset>/... paths. The actual binaries are gitignored
# and live only in the main checkout. A fresh worktree therefore serves
# 404 for every asset preview until those paths are populated.
#
# This script is idempotent — re-running it skips entries that already
# exist. Run once per new worktree:
#
#     bash tools/setup_worktree_data_links.sh
#
# Spec: docs/CrawlDataAndViewer.md §worktree-setup.
set -euo pipefail
shopt -s nullglob

# Locate the main checkout. Worktrees live at
# <main>/.claude/worktrees/<branch>/ and share <main>/.git.
WT="$(cd "$(dirname "$0")/.." && pwd)"
MAIN="$(git -C "$WT" rev-parse --git-common-dir)"
MAIN="$(cd "$(dirname "$MAIN")" && pwd)"

if [ "$WT" = "$MAIN" ]; then
  echo "refusing to run in the main checkout (no worktree to populate)" >&2
  exit 1
fi

linked=0

# Top-level out_samples (entirely missing from worktrees, gitignored).
if [ ! -e "$WT/out_samples" ] && [ -d "$MAIN/out_samples" ]; then
  ln -s "$MAIN/out_samples" "$WT/out_samples"
  echo "linked: out_samples"
  # Also add to the worktree-local exclude so the symlink (which git sees
  # as a file, not a directory) doesn't show up in `git status`.
  excl="$(git -C "$WT" rev-parse --git-path info/exclude)"
  grep -qxF 'out_samples' "$excl" 2>/dev/null || echo 'out_samples' >> "$excl"
  linked=$((linked + 1))
fi

# Per-domain L2 subdirs and files under crawl_data/. The `broken/` subdirs
# are tracked in-tree and must not be shadowed.
if [ -d "$MAIN/crawl_data" ]; then
  for d in "$MAIN"/crawl_data/*/; do
    domain="$(basename "$d")"
    [ "$domain" = "_logs" ] && continue
    mkdir -p "$WT/crawl_data/$domain"
    for entry in "$d"*; do
      name="$(basename "$entry")"
      [ "$name" = "broken" ] && continue
      [ -e "$WT/crawl_data/$domain/$name" ] && continue
      ln -s "$entry" "$WT/crawl_data/$domain/$name"
      echo "linked: crawl_data/$domain/$name"
      linked=$((linked + 1))
    done
  done
fi

# Per-dataset subdirs under datasets/. `_matched/` is the in-tree exception.
if [ -d "$MAIN/datasets" ]; then
  for d in "$MAIN"/datasets/*/; do
    name="$(basename "$d")"
    [ "$name" = "_matched" ] && continue
    [ "$name" = "_logs" ] && continue
    [ -e "$WT/datasets/$name" ] && continue
    ln -s "$d" "$WT/datasets/$name"
    echo "linked: datasets/$name"
    linked=$((linked + 1))
  done
  for f in "$MAIN"/datasets/*; do
    [ -f "$f" ] || continue
    name="$(basename "$f")"
    [ -e "$WT/datasets/$name" ] && continue
    ln -s "$f" "$WT/datasets/$name"
    echo "linked: datasets/$name"
    linked=$((linked + 1))
  done
fi

echo "done. linked $linked new path(s)."
