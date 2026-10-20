#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TASKGEN_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${TASKGEN_ROOT}"
PYTHONPATH="${TASKGEN_ROOT}:${PYTHONPATH:-}" python3 -m sciencevision_taskgen.runner "$@"
