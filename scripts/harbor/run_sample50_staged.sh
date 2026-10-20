#!/usr/bin/env bash
# Run the scale-v1 baselines over the 130 tasks with validated deterministic GT.
# The 20 docking tasks without signed expert GT are excluded, so 130 x 6 = 780 trials.
#
# Baseline matrix: the 6 of run_scale_v1_staged.sh — cc / ccmcp / cc2d-{1,3,6,12}.
# The sci3d host-run scaffold (`host`) is wired and smoke-tested but NOT in the
# default matrix; opt in explicitly when you want the specialized-vs-generic
# contrast from HARNESS_COMPARISON:
#   BASELINES="host" bash scripts/harbor/run_sample50_staged.sh
#
# Per-baseline agent wiring differs: CC/CC-MCP use the route-contract Codex
# adapter (which delegates to Harbor's Codex after writing the health receipt),
# while CC2D uses Harbor's built-in Codex and `host` needs
# `--agent-import-path harbor_adapters.sci3d.agent:Sci3dAgent`. Everything else
# (model, deterministic verifier, timeouts) is shared.
#
# CONCURRENCY / docker address pool. Each trial builds a compose network, and
# docker's built-in default pool has only ~31 subnets; other long-running jobs
# on this host hold ~20. A `-n 24` run on 2026-07-31 exhausted the pool and
# failed 1549/1567 trials with "could not find an available, non-overlapping
# IPv4 address pool". The preflight below refuses to start when free headroom is
# below -n, so we fail in one second instead of burning the whole matrix. -n is
# overridable: `N_CONCURRENT=4 bash scripts/harbor/run_sample50_staged.sh`.
#
# Formal scoring is deterministic and performs no judge-model network call.
# Optional judge scorers are explicit shadow diagnostics only.
#
# Codex auth reaches the container via --ae. CODEX_MODEL is mandatory and is
# recorded without credential material before Docker preflight starts.
# env -u <proxies>: the shell default enables an HTTP proxy that misroutes the
# internal ALB host.
source ~/.bashrc 2>/dev/null || true
set -o pipefail
cd "$(dirname "$0")/../.."   # worktree root
BL="$(pwd)"
source scripts/harbor/codex_runtime.sh

codex_resolve_harbor || exit
HARBOR="$CODEX_HARBOR_BIN"
PYTHON="${HARBOR_PYTHON:-$(command -v python3 || true)}"
[ -n "$PYTHON" ] || { echo "ABORT: python3 is not on PATH" >&2; exit 2; }
# Overridable so QC/reproduction runs can use an isolated staged tree.
JOBS="${JOBS:-jobs-sample50}"
LOG="$JOBS/run.log"
N_CONCURRENT="${N_CONCURRENT:-6}"
BASELINES="${BASELINES:-cc ccmcp cc2d-1 cc2d-3 cc2d-6 cc2d-12}"
# Docker's built-in default address pool: 172.17-172.31/16 (15) + 192.168.0-240/20 (16).
POOL_TOTAL=31

mkdir -p "$JOBS"
codex_runtime_preflight "$JOBS" || exit

# ── preflight: docker address-pool headroom ─────────────────────────────── #
# Every bridge network (ours and other tenants') consumes one pool subnet.
used=$(docker network ls --filter driver=bridge -q | wc -l)
free=$(( POOL_TOTAL - used ))
echo "docker address pool: ${used} bridge networks in use, ~${free} of ${POOL_TOTAL} free; -n ${N_CONCURRENT}"
if [ "$free" -lt "$N_CONCURRENT" ]; then
  echo "ABORT: free pool headroom (${free}) < -n (${N_CONCURRENT}). Lower N_CONCURRENT" >&2
  echo "       or wait for other jobs to drain. Do NOT restart docker to expand" >&2
  echo "       the pool: it kills every container on this host, including other" >&2
  echo "       tenants' multi-day runs." >&2
  exit 1
fi

COMMON=(
  --verifier-import-path harbor_adapters.sci3d.verifier:Sci3dVerifier
  --agent-setup-timeout-multiplier 3.0
  --agent-timeout-multiplier 2.0
  --env docker
  -n "$N_CONCURRENT"
)

echo "=== sample50 staged run start: $(date -u +%FT%TZ) (baselines: $BASELINES) ===" | tee -a "$LOG"
for b in $BASELINES; do
  if [ ! -d "$JOBS/tasks-$b" ]; then
    echo "!!! SKIP $b: $JOBS/tasks-$b missing" | tee -a "$LOG"
    continue
  fi
  # Resume: skip tasks that already have a finished trial in an earlier run of
  # this baseline, so a relaunch (crash, kill, or a concurrency change like
  # -n 6 -> -n 8) does not re-pay for them. Trials that timed out count as
  # finished — an AgentTimeoutError is a real measurement of the baseline, not a
  # transient. `--exclude-task-name` is fnmatch'd against harbor's task name, so
  # the helper emits `*<task_id>` to match both the bare id and the
  # `sci3d/<task_id>` form from task.toml.
  EXCLUDE=()
  n_done=0
  while IFS= read -r pat; do
    [ -n "$pat" ] || continue
    EXCLUDE+=(--exclude-task-name "$pat")
    # Count patterns, NOT ${#EXCLUDE[@]}: each task adds two array elements
    # (the flag and its value), which would double the tally and make the
    # already-complete guard below fire at half the tasks.
    n_done=$(( n_done + 1 ))
  done < <("$PYTHON" -m scripts.harbor.completed_task_ids \
             --job-root "$JOBS/job-$b" --as-glob 2>/dev/null)
  n_total=$(find "$JOBS/tasks-$b" -mindepth 1 -maxdepth 1 -type d | wc -l)
  if [ "$n_done" -ge "$n_total" ]; then
    echo "=========== BASELINE $b already complete ($n_done/$n_total), skipping ===========" | tee -a "$LOG"
    continue
  fi
  [ "$n_done" -gt 0 ] && echo "resuming $b: $n_done/$n_total already done, running $(( n_total - n_done ))" | tee -a "$LOG"

  # host: optional in-process scaffold. The six leaderboard rows use Codex.
  if [ "$b" = "host" ]; then
    AGENT_FLAGS=(--agent-import-path harbor_adapters.sci3d.agent:Sci3dAgent
                 --model "$CODEX_RUNTIME_MODEL"
                 --ak "mode=mode1_no_vision" --ak "max_iters=20")
  else
    case "$b" in
      cc|ccmcp|cc2d-1|cc2d-3|cc2d-6|cc2d-12) ;;
      *) echo "ABORT: unknown baseline: $b" >&2; exit 2 ;;
    esac
    codex_validate_task_tree "$b" "$JOBS/tasks-$b" || exit
    codex_args_for_baseline "$b"
    AGENT_FLAGS=("${CODEX_BASELINE_AGENT_ARGS[@]}")
  fi
  echo "=========== BASELINE $b start $(date -u +%FT%TZ) ===========" | tee -a "$LOG"
  env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy \
      -u HTTPS_PROXY -u https_proxy \
      PYTHONPATH="$BL" \
    "$HARBOR" run --path "$JOBS/tasks-$b" "${AGENT_FLAGS[@]}" "${COMMON[@]}" \
    "${EXCLUDE[@]+"${EXCLUDE[@]}"}" -o "$JOBS/job-$b" \
    2>&1 | tee -a "$LOG" || echo "!!! BASELINE $b exited nonzero (continuing)" | tee -a "$LOG"
  echo "=========== BASELINE $b end $(date -u +%FT%TZ) ===========" | tee -a "$LOG"
done
echo "=== sample50 staged run end: $(date -u +%FT%TZ) ===" | tee -a "$LOG"
