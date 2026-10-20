#!/usr/bin/env bash
set -euo pipefail

IMAGE="${1:-sci3d-codex-2d:latest}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKERFILE="${SCRIPT_DIR}/Dockerfile.codex-2d"

echo "Building ${IMAGE} from ${DOCKERFILE}"
docker build --platform linux/amd64 -t "${IMAGE}" -f "${DOCKERFILE}" "${SCRIPT_DIR}"
echo "Done. Reference it as: FROM ${IMAGE}"
