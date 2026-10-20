#!/bin/bash
# Build the prebuilt Claude Code agent image used by the claude-code harness.
#
# Tasks generated with `qa_gen_to_tasks --agent-env claude-code` reference this
# image by name (FROM sci3d-claude-code:latest) in their environment/Dockerfile,
# so `harbor run --agent claude-code` starts instantly instead of bootstrapping
# the CLI per trial (which is blocked on the H20 host).
#
#   bash scripts/harbor/templates/build_claude_code_image.sh [IMAGE_TAG]
set -euo pipefail

IMAGE="${1:-sci3d-claude-code:latest}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKERFILE="${SCRIPT_DIR}/Dockerfile.claude-code"

echo "Building ${IMAGE} from ${DOCKERFILE}"
docker build --platform linux/amd64 -t "${IMAGE}" -f "${DOCKERFILE}" "${SCRIPT_DIR}"
echo "Done. Reference it as: FROM ${IMAGE}"
