#!/usr/bin/env bash
set -euo pipefail

IMAGE="${1:-sci3d-codex-mcp:latest}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
DOCKERFILE="${SCRIPT_DIR}/Dockerfile.codex-mcp"
BASE_IMAGE="${SCI3D_CODEX_AGENT_BASE_IMAGE:-sci3d-codex:latest}"
SOURCE_REVISION="$(git -C "${REPO_ROOT}" rev-parse --verify 'HEAD^{commit}')"

echo "Building ${IMAGE}"
echo "  base:       ${BASE_IMAGE}"
echo "  context:    ${SCRIPT_DIR}"
echo "  dockerfile: ${DOCKERFILE}"
if ! docker image inspect "${BASE_IMAGE}" >/dev/null 2>&1; then
    echo "Base image is absent; building ${BASE_IMAGE} first"
    bash "${SCRIPT_DIR}/build_codex_image.sh" "${BASE_IMAGE}"
fi
docker build --platform linux/amd64 \
    --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
    --build-arg "SCI3D_SOURCE_REVISION=${SOURCE_REVISION}" \
    -t "${IMAGE}" -f "${DOCKERFILE}" "${SCRIPT_DIR}"
echo "Done. Reference it as: FROM ${IMAGE}"
