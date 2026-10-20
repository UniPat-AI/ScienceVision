#!/bin/bash
# Build the shared sci3d scorer image used by the containerized Harbor route.
#
# Harbor pins each task's per-task Docker build context to that task's
# environment/ directory, which cannot COPY the repo. So instead of a per-task
# Dockerfile we build ONE lightweight scorer image from the repo root and have
# every containerized task reference it by name in
#   [verifier.environment].docker_image = "suermars/sci3d-scorer:server-20260930-h20"
#
# Run from anywhere; the script resolves the repo root itself.
#   bash scripts/harbor/templates/build_image.sh [IMAGE_TAG]
# Default tag: suermars/sci3d-scorer:server-20260930-h20 (the newest published
# release; the old default, sci3d-scorer:harbor-0.20-v1, exists in no registry)
set -euo pipefail

DEFAULT_IMAGE="suermars/sci3d-scorer:server-20260930-h20"
IMAGE="${1:-${DEFAULT_IMAGE}}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
DOCKERFILE="${SCRIPT_DIR}/Dockerfile"
SOURCE_REVISION="$(git -C "${REPO_ROOT}" rev-parse --verify 'HEAD^{commit}')"

IMAGE_NAME="${IMAGE##*/}"
if [[ "${IMAGE_NAME}" != *:* || "${IMAGE_NAME##*:}" == "latest" ]]; then
    echo "Refusing unversioned scorer image tag: ${IMAGE}" >&2
    exit 2
fi
if [[ -n "$(git -C "${REPO_ROOT}" status --porcelain --untracked-files=all)" ]]; then
    echo "Refusing to label an image from a dirty source tree: ${REPO_ROOT}" >&2
    exit 2
fi

inspect_revision() {
    docker image inspect \
        --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' \
        "${IMAGE}"
}

inspect_id() {
    docker image inspect --format '{{.Id}}' "${IMAGE}"
}

print_provenance() {
    local image_id="$1"
    printf 'SCI3D_SCORER_IMAGE=%s\n' "${IMAGE}"
    printf 'SCI3D_SCORER_IMAGE_ID=%s\n' "${image_id}"
    printf 'SCI3D_SCORER_SOURCE_REVISION=%s\n' "${SOURCE_REVISION}"
}

if docker image inspect "${IMAGE}" >/dev/null 2>&1; then
    existing_revision="$(inspect_revision)"
    if [[ "${existing_revision}" != "${SOURCE_REVISION}" ]]; then
        echo "Refusing scorer image tag with mismatched source revision: ${IMAGE}" >&2
        echo "  expected: ${SOURCE_REVISION}" >&2
        echo "  actual:   ${existing_revision:-<missing label>}" >&2
        echo "Choose a new versioned tag." >&2
        exit 2
    fi
    image_id="$(inspect_id)"
    if [[ -z "${image_id}" ]]; then
        echo "Scorer image has no inspectable image ID: ${IMAGE}" >&2
        exit 2
    fi
    echo "Existing scorer image matches this source revision."
    print_provenance "${image_id}"
    exit 0
fi

echo "Building ${IMAGE}"
echo "  context:    ${REPO_ROOT}"
echo "  dockerfile: ${DOCKERFILE}"

IID_FILE="$(mktemp)"
trap 'rm -f "${IID_FILE}"' EXIT
docker build \
    --platform linux/amd64 \
    --build-arg "SCI3D_SOURCE_REVISION=${SOURCE_REVISION}" \
    --iidfile "${IID_FILE}" \
    -t "${IMAGE}" \
    -f "${DOCKERFILE}" \
    "${REPO_ROOT}"

build_id="$(<"${IID_FILE}")"
image_id="$(inspect_id)"
image_revision="$(inspect_revision)"
if [[ -z "${build_id}" || "${image_id}" != "${build_id}" ]]; then
    echo "Built image ID does not match the tagged image ID: ${IMAGE}" >&2
    exit 2
fi
if [[ "${image_revision}" != "${SOURCE_REVISION}" ]]; then
    echo "Built image source revision label failed verification: ${IMAGE}" >&2
    exit 2
fi

print_provenance "${image_id}"
echo "Done. Reference it in containerized task.toml as:"
echo "    [verifier.environment]"
echo "    docker_image = \"${IMAGE}\""
