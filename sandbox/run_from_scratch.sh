#!/usr/bin/env bash
# Build the harness from source, build task trees from a dataset + contracts,
# pull the pinned scorer, and replay archived answers. Everything lands under
# $WORK; nothing outside it is modified.
set -euo pipefail

: "${WORK:?set WORK to a fresh empty directory}"
: "${DATASET:?set DATASET to the dataset root}"
: "${CONTRACTS:?set CONTRACTS to the exported contracts.jsonl}"
: "${SCORER_IMAGE:?set SCORER_IMAGE to a scorer image reference (prefer @sha256:...)}"

REPO=${REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
PYTHON=${PYTHON:-python3.12}
VENV=${VENV:-$WORK/venv}

if [[ $# -eq 0 ]]; then
  echo "usage: $0 <archived-job-dir> [archived-job-dir ...]" >&2
  exit 2
fi

mkdir -p "$WORK"
"$PYTHON" -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e "${REPO}[harbor]"

pids=()
for harness in codex claude-code; do
  for route in cc ccmcp; do
    "$VENV/bin/python" -m sandbox.build_trees \
      --contracts "$CONTRACTS" \
      --dataset-root "$DATASET" \
      --out "$WORK/trees-$harness" \
      --route "$route" \
      --harness "$harness" &
    pids+=("$!")
  done
done
for pid in "${pids[@]}"; do
  wait "$pid"
done

docker pull "$SCORER_IMAGE"

args=(--scorer-image "$SCORER_IMAGE" --output "$WORK/replay-summary.json" --jsonl "$WORK/replay-trials.jsonl")
for harness in codex claude-code; do
  args+=(--task-root "$WORK/trees-$harness")
done
for job in "$@"; do
  args+=(--job "$job")
done

"$VENV/bin/python" -m sandbox.replay "${args[@]}"
echo "summary: $WORK/replay-summary.json"
