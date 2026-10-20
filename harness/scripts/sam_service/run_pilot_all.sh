#!/usr/bin/env bash
# Orchestrate the SAM-as-tool ablation pilot across quality tiers.
# For each tier (perfect/noisy/wrong): (re)start fake SAM server at that
# tier, then run the chosen experiment driver. Emits one JSON per (exp,tier).
#
# Usage: bash run_pilot_all.sh <n> <model>
set +e
cd /home/matianyi/Science-Vision/.claude/worktrees/verify-sam-tool-env

N="${1:-10}"
MODEL="${2:-claude-sonnet-4-6}"
MAXITERS="${3:-18}"
VENV="$PWD/.venv"
OUTDIR="evaluation_runs/ablation_pilot"
mkdir -p "$OUTDIR"

# Auth must be provided by the caller via env:
#   ANTHROPIC_API_KEY (bridged from ANTHROPIC_AUTH_TOKEN), and proxy cleared.
export SAM_SERVICE_URL="http://localhost:8900"
export VIRTUAL_ENV="$VENV"
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then echo "FATAL: ANTHROPIC_API_KEY unset"; exit 2; fi

start_server() {  # $1 = tier
  pkill -f 'scripts.sam_service.fake_server' 2>/dev/null
  sleep 1
  FAKE_SAM_QUALITY="$1" "$VENV/bin/python" -m scripts.sam_service.fake_server \
      > "/tmp/fake_sam_${1}.log" 2>&1 &
  for _ in $(seq 1 40); do
    curl -s --noproxy '*' http://localhost:8900/healthz | grep -q "$1" && return 0
    sleep 0.3
  done
  echo "SERVER_FAIL tier=$1"; return 1
}

run() {  # $1=experiment $2=tier
  local exp="$1" tier="$2"
  local out="$OUTDIR/${exp}_${tier}.json"
  echo ">>> RUN exp=$exp tier=$tier n=$N model=$MODEL max_iters=$MAXITERS"
  "$VENV/bin/python" -m scripts.sam_service.run_ablation_pilot \
      --experiment "$exp" --tier "$tier" --n "$N" --model "$MODEL" \
      --max-iters "$MAXITERS" --out "$out" 2>&1
  echo "<<< DONE exp=$exp tier=$tier"
}

for tier in perfect noisy wrong; do
  start_server "$tier" || exit 1
  for exp in e2_volume e1_mesh; do
    run "$exp" "$tier"
  done
done

pkill -f 'scripts.sam_service.fake_server' 2>/dev/null
echo "ALL_PILOT_DONE"
