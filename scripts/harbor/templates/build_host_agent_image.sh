#!/bin/bash
# Create the local sci3d-host-agent image tag for the host-run route.
#
# The host-run Sci3dAgent executes in the orchestrator process, so its
# container only needs to exist. We pull python:3.12-slim ONCE and tag it
# sci3d-host-agent:latest; each host task's environment/Dockerfile does
# `FROM sci3d-host-agent:latest`, so harbor's per-task env start uses the local
# image and never pulls from the registry (a per-task pull of python:3.12-slim
# timed out the first task on the slow H20 Docker network).
#
#   bash scripts/harbor/templates/build_host_agent_image.sh
set -euo pipefail

BASE="${1:-python:3.12-slim}"
TAG="${2:-sci3d-host-agent:latest}"

echo "Pulling ${BASE} (once) and tagging as ${TAG}"
docker pull "${BASE}"
docker tag "${BASE}" "${TAG}"
echo "Done. Host tasks reference it as: FROM ${TAG}"
