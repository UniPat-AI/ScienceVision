#!/usr/bin/env bash
# Build only a local candidate. Registry publication is deliberately separate.
set -euo pipefail

IMAGE="${1:-suermars/sci3d-scorer:server-20260923-h20}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONTEXT_DIR="$(mktemp -d)"
IID_FILE="$(mktemp)"

cleanup() {
    rm -rf "${CONTEXT_DIR}"
    rm -f "${IID_FILE}"
}
trap cleanup EXIT

if ! command -v docker >/dev/null 2>&1; then
    echo "docker is required to build ${IMAGE}" >&2
    exit 127
fi

SOURCE_REVISION="$(git -C "${REPO_ROOT}" rev-parse --verify 'HEAD^{commit}')"
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(git -C "${REPO_ROOT}" show -s --format=%ct HEAD)}"
python3 "${SCRIPT_DIR}/build_context.py" --repo-root "${REPO_ROOT}" --output "${CONTEXT_DIR}"
MANIFEST_SHA256="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["manifest_sha256"])' "${CONTEXT_DIR}/scorer_manifest.json")"

echo "Building local candidate ${IMAGE}"
echo "  source revision: ${SOURCE_REVISION}"
echo "  scorer manifest: ${MANIFEST_SHA256}"
echo "  context: explicit formal-scorer allow-list; no agent/MCP/dataset files"
docker build \
    --platform linux/amd64 \
    --network=none \
    --build-arg "SCI3D_SOURCE_REVISION=${SOURCE_REVISION}" \
    --build-arg "SCI3D_SCORER_MANIFEST_SHA256=${MANIFEST_SHA256}" \
    --iidfile "${IID_FILE}" \
    -t "${IMAGE}" \
    -f "${CONTEXT_DIR}/Dockerfile" \
    "${CONTEXT_DIR}"

IMAGE_ID="$(docker image inspect --format '{{.Id}}' "${IMAGE}")"
if [[ ! -s "${IID_FILE}" || "$(<"${IID_FILE}")" != "${IMAGE_ID}" ]]; then
    echo "built image ID does not match tagged image: ${IMAGE}" >&2
    exit 2
fi
REPO_DIGEST="$(docker image inspect --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{else}}unpublished-local-image{{end}}' "${IMAGE}")"
printf 'SCI3D_SCORER_IMAGE=%s\n' "${IMAGE}"
printf 'SCI3D_SCORER_IMAGE_ID=%s\n' "${IMAGE_ID}"
printf 'SCI3D_SCORER_IMAGE_REPO_DIGEST=%s\n' "${REPO_DIGEST}"
printf 'SCI3D_SCORER_MANIFEST_SHA256=%s\n' "${MANIFEST_SHA256}"
echo "Built locally only; no registry push was attempted."
