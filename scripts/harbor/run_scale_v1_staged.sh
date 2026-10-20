#!/usr/bin/env bash
# Run the 6 scale-v1 baselines directly over the ALREADY-STAGED task trees
# under jobs-scale-v1/tasks-{b} (built once, ~2h, cached). Skips re-staging.
# Baselines run sequentially; each harbor run uses -n 8 so at most 8 trial
# containers/networks are in flight. NOTE: docker's default address pool only
# has ~31 subnets and other long-running jobs on this host occupy ~20, leaving
# ~10 free. -n 24 exhausted the pool ("no available, non-overlapping IPv4
# address pool") and failed ~99% of trials. Keep -n <= free headroom until the
# daemon default-address-pools is expanded (needs a docker restart).
# One baseline failing does not abort the rest
# (|| true), so partial results are always collectable.
#
# Formal scoring is deterministic and performs no judge-model network call.
# Optional judge scorers are explicit shadow diagnostics only.
#
# CODEX_MODEL and one supported Codex auth source are mandatory. The resolved
# non-secret runtime metadata is recorded before any baseline starts.
# env -u <proxies>: the shell default enables an HTTP proxy that misroutes the
# internal ALB host.
source ~/.bashrc 2>/dev/null || true
set -o pipefail
cd "$(dirname "$0")/../.."   # worktree root
BL="$(pwd)"
source scripts/harbor/codex_runtime.sh

codex_resolve_harbor || exit
HARBOR="$CODEX_HARBOR_BIN"
JOBS=jobs-scale-v1
LOG="$JOBS/run.log"
codex_runtime_preflight "$JOBS" || exit

COMMON=(
  --verifier-import-path harbor_adapters.sci3d.verifier:Sci3dVerifier
  --agent-setup-timeout-multiplier 3.0
  --agent-timeout-multiplier 2.0
  --env docker
  -n 8
)

echo "=== scale-v1 staged run start: $(date -u +%FT%TZ) ===" | tee -a "$LOG"
for b in cc ccmcp cc2d-1 cc2d-3 cc2d-6 cc2d-12; do
  codex_validate_task_tree "$b" "$JOBS/tasks-$b" || exit
  codex_args_for_baseline "$b"
  echo "=========== BASELINE $b start $(date -u +%FT%TZ) ===========" | tee -a "$LOG"
  env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy \
      -u HTTPS_PROXY -u https_proxy \
      PYTHONPATH="$BL" \
    "$HARBOR" run --path "$JOBS/tasks-$b" "${CODEX_BASELINE_AGENT_ARGS[@]}" \
    "${COMMON[@]}" -o "$JOBS/job-$b" \
    2>&1 | tee -a "$LOG" || echo "!!! BASELINE $b exited nonzero (continuing)" | tee -a "$LOG"
  echo "=========== BASELINE $b end $(date -u +%FT%TZ) ===========" | tee -a "$LOG"
done
echo "=== scale-v1 staged run end: $(date -u +%FT%TZ) ===" | tee -a "$LOG"
