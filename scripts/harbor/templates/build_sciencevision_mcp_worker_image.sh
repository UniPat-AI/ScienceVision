#!/usr/bin/env bash
# Build the private MCP worker from a minimal, explicit Docker context.
#
# This avoids sending datasets, generated task trees, credentials, or unrelated
# checkout content across the Docker client/daemon boundary. It builds locally
# only; pushing remains a separate explicitly authorized action.
set -euo pipefail

IMAGE="${1:-sci3d-mcp-worker:dev}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
SOURCE_REVISION="$(git -C "${REPO_ROOT}" rev-parse --verify 'HEAD^{commit}')"
WORKER_CONTEXT_DIR="$(mktemp -d)"
IID_FILE="$(mktemp)"

cleanup() {
    rm -rf "${WORKER_CONTEXT_DIR}"
    rm -f "${IID_FILE}"
}
trap cleanup EXIT

cp "${REPO_ROOT}/pyproject.toml" "${REPO_ROOT}/README.md" "${WORKER_CONTEXT_DIR}/"
cp -R "${REPO_ROOT}/sci3d_agent" "${REPO_ROOT}/harbor_adapters" "${REPO_ROOT}/sci3d_bench" "${WORKER_CONTEXT_DIR}/"
# Compatibility-only catalog input.  Do not broaden this to viewer/ or
# datasets/: the worker needs the global registry and matched-QA records, not
# raw assets, viewer application code, or any unrelated benchmark data.
mkdir -p "${WORKER_CONTEXT_DIR}/viewer" "${WORKER_CONTEXT_DIR}/datasets"
cp "${REPO_ROOT}/viewer/assets.json" "${WORKER_CONTEXT_DIR}/viewer/assets.json"
cp -R "${REPO_ROOT}/datasets/_matched" "${WORKER_CONTEXT_DIR}/datasets/_matched"
cp "${SCRIPT_DIR}/Dockerfile.sciencevision-mcp-worker" "${WORKER_CONTEXT_DIR}/Dockerfile"
cp "${SCRIPT_DIR}/mcp_worker_entrypoint.py" "${SCRIPT_DIR}/mcp_worker_healthcheck.py" "${WORKER_CONTEXT_DIR}/"

echo "Building ${IMAGE}"
echo "  source revision: ${SOURCE_REVISION}"
echo "  context: staged allowlist (pyproject, README, sci3d_agent, harbor_adapters, sci3d_bench, viewer/assets.json, datasets/_matched)"
docker build \
    --platform linux/amd64 \
    --build-arg "SCI3D_SOURCE_REVISION=${SOURCE_REVISION}" \
    --iidfile "${IID_FILE}" \
    -t "${IMAGE}" \
    -f "${WORKER_CONTEXT_DIR}/Dockerfile" \
    "${WORKER_CONTEXT_DIR}"

IMAGE_ID="$(docker image inspect --format '{{.Id}}' "${IMAGE}")"
if [[ ! -s "${IID_FILE}" || "$(<"${IID_FILE}")" != "${IMAGE_ID}" ]]; then
    echo "Built image ID does not match tagged image: ${IMAGE}" >&2
    exit 2
fi
printf 'SCI3D_MCP_WORKER_IMAGE=%s\n' "${IMAGE}"
printf 'SCI3D_MCP_WORKER_IMAGE_ID=%s\n' "${IMAGE_ID}"
printf 'SCI3D_MCP_WORKER_SOURCE_REVISION=%s\n' "${SOURCE_REVISION}"
echo "Built locally only; no registry push was attempted."
