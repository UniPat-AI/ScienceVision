#!/bin/bash
# Build the source-free Claude Code agent for the HTTP MCP harness. The
# ScienceVision server itself is built separately as sci3d-mcp-worker.
#
# Tasks generated with `qa_gen_to_tasks --agent-env claude-code-mcp` reference
# this image by name (FROM sci3d-claude-code-mcp:latest) in their
# environment/Dockerfile, so `harbor run --agent claude-code` starts instantly
# instead of bootstrapping the CLI / pip-installing the repo per trial.
#
# Build context is templates/ because this wrapper intentionally contains no
# repository source. The worker build script stages its own minimal context.
#
#   bash scripts/harbor/templates/build_claude_code_mcp_image.sh [IMAGE_TAG]
set -euo pipefail

IMAGE="${1:-sci3d-claude-code-mcp:latest}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
DOCKERFILE="${SCRIPT_DIR}/Dockerfile.claude-code-mcp"
BASE_IMAGE="${SCI3D_CLAUDE_AGENT_BASE_IMAGE:-sci3d-claude-code:latest}"
SOURCE_REVISION="$(git -C "${REPO_ROOT}" rev-parse --verify 'HEAD^{commit}')"

echo "Building ${IMAGE}"
echo "  base:       ${BASE_IMAGE}"
echo "  context:    ${SCRIPT_DIR}"
echo "  dockerfile: ${DOCKERFILE}"
if ! docker image inspect "${BASE_IMAGE}" >/dev/null 2>&1; then
    echo "Base image is absent; building ${BASE_IMAGE} first"
    bash "${SCRIPT_DIR}/build_claude_code_image.sh" "${BASE_IMAGE}"
fi
docker build \
    --platform linux/amd64 \
    --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
    --build-arg "SCI3D_SOURCE_REVISION=${SOURCE_REVISION}" \
    -t "${IMAGE}" \
    -f "${DOCKERFILE}" \
    "${SCRIPT_DIR}"

echo "Done. Reference it as: FROM ${IMAGE}"
