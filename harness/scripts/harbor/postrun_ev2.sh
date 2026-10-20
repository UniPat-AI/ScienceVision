#!/usr/bin/env bash
# Post-run EV2 pipeline: turn the fresh 3-baseline harbor job dirs into the
# authoritative deterministic artifacts.
#
# Order matters:
#   1. threeway  — docs/harbor/EV2_THREEWAY.{md,json} (per-task content/process
#                  x baseline; tiers rescored > degraded).
#   2. viewer    — viewer/verify_ev2_pilot.json (answers + trajectory per task).
#   3. collect   — QA-Gen/tasks/EV2_Pilot/runs/<slug>.json + copied render
#                  screenshots + QA-Gen/tasks/runs_leaderboard.{json,md}
#                  (rewards preferred from the rescored file).
# All steps only read job dirs. CODEX_MODEL is required to label collected runs.
set -o pipefail
cd "$(dirname "$0")/../.."   # worktree repo root
: "${CODEX_MODEL:?CODEX_MODEL is required to label EV2 artifacts}"

PY="${HARBOR_PYTHON:-$(command -v python3 || true)}"
[ -n "$PY" ] || { echo "ABORT: python3 is not on PATH" >&2; exit 2; }
source scripts/harbor/codex_runtime.sh
codex_verify_runtime_record jobs-ev2-codex-runtime "$CODEX_MODEL" "$PY" || exit
if compgen -G 'jobs-ev2-codex-cc-rerun/*/' >/dev/null \
    || compgen -G 'jobs-ev2-codex-cc2d-rerun/*/' >/dev/null \
    || compgen -G 'jobs-ev2-codex-ccmcp-rerun/*/' >/dev/null; then
  codex_verify_runtime_record jobs-ev2-codex-rerun-runtime "$CODEX_MODEL" "$PY" || exit
fi

# Pick the latest job dir LEXICALLY (sort | tail -1), matching every Python
# reader's `sorted(glob(...))[-1]`. Timestamped dir names sort chronologically,
# and staying lexical (not mtime `ls -dt`) means a restored / `mv`'d dir or
# clock skew can't make collect process a different dir than rescore/report —
# which would silently fall back to the 0.100 floor and desync the artifacts.
latest() { ls -d "jobs-ev2-codex-$1"/*/ 2>/dev/null | sort | tail -1; }

echo "=========== 1/3 three-way report ==========="
"$PY" -m scripts.harbor.report_ev2_threeway

echo "=========== 2/3 viewer bundle ==========="
"$PY" -m scripts.harbor.build_ev2_viewer_bundle

echo "=========== 3/3 collect runs (answer + trajectory + screenshots) ==========="
RUN_ARGS=()
for pair in "cc:codex" "cc2d:codex-2d" "ccmcp:codex-mcp"; do
  b="${pair%%:*}"; harness="${pair##*:}"
  d="$(latest "$b")"
  if [ -z "$d" ]; then echo "  WARN: no job dir for $b"; continue; fi
  RUN_ARGS+=(--run "${harness}:${CODEX_RUNTIME_MODEL}:${d%/}")
done
"$PY" -m scripts.harbor.collect_qagen_results "${RUN_ARGS[@]}" \
    --tasks-root QA-Gen/tasks

echo "=========== EV2 POST-RUN PIPELINE DONE ==========="
echo "artifacts:"
echo "  docs/harbor/EV2_THREEWAY.{md,json}"
echo "  viewer/verify_ev2_pilot.json"
echo "  QA-Gen/tasks/EV2_Pilot/runs/  +  QA-Gen/tasks/runs_leaderboard.{md,json}"
