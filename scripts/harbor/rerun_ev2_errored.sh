#!/usr/bin/env bash
# Re-run the errored ev2 trials for the 3 baselines.
# Uses the same explicit Codex runtime preflight as the full EV2 runner.
# Each baseline is filtered to only its errored task(s) via -i.
# NOTE: source ~/.bashrc BEFORE `set -u` — /etc/bashrc references an unbound
# BASHRCSOURCED under `set -u` and would kill the script on entry.
source ~/.bashrc 2>/dev/null || true
set -o pipefail
cd "$(dirname "$0")/../.."   # repo root of the worktree
source scripts/harbor/codex_runtime.sh
EV2_CC_TASKS="${EV2_CC_TASKS:-scripts/harbor/tasks-ev2-cc}"
EV2_CC2D_TASKS="${EV2_CC2D_TASKS:-scripts/harbor/tasks-ev2-cc2d}"
EV2_CCMCP_TASKS="${EV2_CCMCP_TASKS:-scripts/harbor/tasks-ev2-ccmcp}"

require_ev2_tree() {
  codex_validate_task_tree "$1" "$2" 15 && return
  echo "       EV2 task trees are generated, gitignored inputs; see" >&2
  echo "       docs/harbor/CODEX_H20_RUNBOOK.md#ev2-three-way-task-trees" >&2
  exit 2
}
require_ev2_tree cc "$EV2_CC_TASKS"
require_ev2_tree cc2d "$EV2_CC2D_TASKS"
require_ev2_tree ccmcp "$EV2_CCMCP_TASKS"

codex_runtime_preflight jobs-ev2-codex-rerun-runtime || exit
codex_resolve_harbor || exit
HARBOR="$CODEX_HARBOR_BIN"
COMMON=(
  --verifier-import-path harbor_adapters.sci3d.verifier:Sci3dVerifier
  --agent-setup-timeout-multiplier 3.0
  --agent-timeout-multiplier 2.0
  --env docker
)

run() {  # $1=baseline  $2=tasks-path  $3=out-dir  rest=-i task filters
  local baseline="$1" path="$2" out="$3"; shift 3
  codex_validate_task_tree "$baseline" "$path" || exit
  codex_args_for_baseline "$baseline"
  echo "=========== RERUN $out ($*) ==========="
  env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy \
      -u HTTPS_PROXY -u https_proxy \
      "$HARBOR" run --path "$path" \
      "${CODEX_BASELINE_AGENT_ARGS[@]}" "${COMMON[@]}" "$@" -o "$out"
}

run cc "$EV2_CC_TASKS" jobs-ev2-codex-cc-rerun \
    -i sv-chem-optimization-vector-06 \
    -i sv-medi-lesion-3d-extent-03 \
    -i sv-medi-mri-arbitrary-plane-02

run cc2d "$EV2_CC2D_TASKS" jobs-ev2-codex-cc2d-rerun \
    -i sv-chem-optimization-vector-06

run ccmcp "$EV2_CCMCP_TASKS" jobs-ev2-codex-ccmcp-rerun \
    -i sv-biol-molecular-collision-check-03 \
    -i sv-chem-optimization-vector-06

echo "=========== ALL RERUNS DONE ==========="
