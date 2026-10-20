#!/usr/bin/env bash
# Pre-QC -> existing 6-baseline Harbor runner -> post-QC.
#
# This wrapper assumes jobs-sample50/tasks-{cc,ccmcp,cc2d-1,cc2d-3,cc2d-6,
# cc2d-12} have already been staged.  The static gate runs before any paid
# model trial.  The post gate requires complete coverage and writes an
# auditable report/review queue without editing the source task JSONL.
set -euo pipefail
cd "$(dirname "$0")/../.."

TASK_SET="${TASK_SET:-QA-Gen/tasks/scale-v1-sample50/ScienceVision_scale-v1-sample50_final_tasks.jsonl}"
JOBS="${JOBS:-jobs-sample50}"
QC_ROOT="${QC_ROOT:-out/qa-qc/scale-v1-sample50}"
BASELINES="${BASELINES:-cc ccmcp cc2d-1 cc2d-3 cc2d-6 cc2d-12}"
SHORTCUT_PROBES="${SHORTCUT_PROBES:-}"

echo "=== QA pre-QC: $TASK_SET ==="
python -m scripts.qa_qc \
  --tasks "$TASK_SET" \
  --suite-name "scale-v1 sample50 pre-QC" \
  --out-dir "$QC_ROOT/pre" \
  --fail-on error

echo "=== six-baseline eval: $BASELINES ==="
JOBS="$JOBS" BASELINES="$BASELINES" bash scripts/harbor/run_sample50_staged.sh

if [ -n "$SHORTCUT_PROBES" ]; then
  echo "=== isolated shortcut probes: $SHORTCUT_PROBES ==="
  JOBS="$JOBS" BASELINES="$SHORTCUT_PROBES" bash scripts/harbor/run_sample50_staged.sh
fi

echo "=== QA post-QC: $JOBS ==="
python -m scripts.qa_qc \
  --tasks "$TASK_SET" \
  --jobs-root "$JOBS" \
  --expected-baselines $BASELINES \
  --suite-name "scale-v1 sample50 post-QC" \
  --out-dir "$QC_ROOT/post" \
  --fail-on error \
  --fail-on-code missing_eval_coverage \
  --fail-on-code unusable_eval_coverage
