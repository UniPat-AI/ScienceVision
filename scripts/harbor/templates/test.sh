#!/bin/bash
# sci3d containerized verifier entrypoint.
#
# Generated per-task by scripts/harbor/build_harbor_tasks.py --containerized.
# Runs INSIDE the verifier container (the image installs this repo, so the
# scorer imports harbor_adapters.sci3d). It exports the task's [metadata] table
# (inlined below as JSON), the task id and instruction, then invokes the shared
# scorer score.py — which writes /logs/verifier/reward.json. The scoring logic
# is identical to the host-run Sci3dVerifier; only the input plumbing differs.
set -euo pipefail

# ── task-specific values inlined by the generator ───────────────────────── #
export SCI3D_TASK_ID='{{TASK_ID}}'
export SCI3D_METADATA='{{METADATA_JSON}}'
read -r -d '' SCI3D_INSTRUCTION <<'SCI3D_INSTRUCTION_EOF' || true
{{INSTRUCTION}}
SCI3D_INSTRUCTION_EOF
export SCI3D_INSTRUCTION

# ── locate the scorer ───────────────────────────────────────────────────── #
# Harbor mounts this task's tests/ at /tests; score.py sits beside this file.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCORE_PY="${SCRIPT_DIR}/score.py"
if [[ ! -f "${SCORE_PY}" ]]; then
    SCORE_PY="/tests/score.py"
fi

python "${SCORE_PY}"
