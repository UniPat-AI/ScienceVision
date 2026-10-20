#!/usr/bin/env bash
# Full scale-v1 6-baseline end2end run: 1567 final_tasks x 6 baselines
# (cc / ccmcp / cc2d-{1,3,6,12}) = 9,402 trials, ~28h at 24 concurrent trials.
#
# Concurrency model: --max-parallel 1 (baselines sequential) x --n-concurrent 8
# => at most 8 trial containers in flight (capped to docker's free address-pool
#    headroom; see run_scale_v1_staged.sh comment), and per-baseline results land
# incrementally (cc first, ~4.7h, then ccmcp, ...).
#
# CODEX_MODEL is explicit and fail-closed in run_end2end. Auth may come from
# OPENAI_API_KEY or a Codex auth JSON already present on the machine running
# Harbor. No auth file is copied to H20 by this launcher.
#   - env -u <all proxies>: the shell default enables an HTTP proxy that
#     misroutes the internal ALB host.
#
source ~/.bashrc 2>/dev/null || true
set -o pipefail
cd "$(dirname "$0")/../.."   # worktree root

MAIN=/home/matianyi/Science-Vision/QA-Gen/tasks
JOBS=jobs-scale-v1
OUT=out-scale-v1
mkdir -p "$JOBS"
LOG="$JOBS/run.log"
PYTHON="${HARBOR_PYTHON:-$(command -v python3 || true)}"
[ -n "$PYTHON" ] || { echo "ABORT: python3 is not on PATH" >&2; exit 2; }

echo "=== scale-v1 full run start: $(date -u +%FT%TZ) ===" | tee -a "$LOG"
env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy \
    -u HTTPS_PROXY -u https_proxy \
  "$PYTHON" -m scripts.harbor.run_end2end \
    --tasks "$MAIN/scale-v1-medical/ScienceVision_scale-v1-medical_final_tasks.jsonl" \
    --tasks "$MAIN/scale-v1-biology/ScienceVision_scale-v1-biology_final_tasks.jsonl" \
    --tasks "$MAIN/scale-v1-chemistry/ScienceVision_scale-v1-chemistry_final_tasks.jsonl" \
    --pre-resolved \
    --set-name scale-v1 \
    --asset-root "$(pwd)" \
    --out-root "$OUT" \
    --jobs-root "$JOBS" \
    --n-concurrent 8 --max-parallel 1 \
    --harbor-extra=--agent-setup-timeout-multiplier --harbor-extra=3.0 \
    --harbor-extra=--agent-timeout-multiplier --harbor-extra=2.0 \
    2>&1 | tee -a "$LOG"
echo "=== scale-v1 full run end: $(date -u +%FT%TZ) exit=$? ===" | tee -a "$LOG"
