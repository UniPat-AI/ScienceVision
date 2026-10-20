#!/bin/bash
# Build the prebuilt agent image for the 2D image multi-view harness.
#
# Tasks generated with `qa_gen_to_tasks --agent-env claude-code-2d` reference
# this image (FROM sci3d-claude-code-2d:latest) in their environment/Dockerfile.
#
#   bash scripts/harbor/templates/build_claude_code_2d_image.sh [IMAGE_TAG]
set -euo pipefail

IMAGE="${1:-sci3d-claude-code-2d:latest}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKERFILE="${SCRIPT_DIR}/Dockerfile.claude-code-2d"

echo "Building ${IMAGE} from ${DOCKERFILE}"
docker build --platform linux/amd64 -t "${IMAGE}" -f "${DOCKERFILE}" "${SCRIPT_DIR}"
echo "Done. Reference it as: FROM ${IMAGE}"
