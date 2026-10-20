# sci3d containerized verifier image (lightweight scoring-only).
#
# This image scores an agent run; it does NOT render or segment. The heavy
# GL / SAM stack is the *agent's* job and lives in the agent's environment.
# The verifier only needs the answer JSON, trajectory JSON, task metadata, and
# deterministic sci3d scorers. Optional judges remain shadow diagnostics.
#
# The image installs this repo so that the in-container scorer can
#   `from harbor_adapters.sci3d._score_core import score_rewards`
# i.e. it runs the EXACT scoring code the host route runs — zero duplication.
#
# Build context = the REPO ROOT (not the task's environment/ dir — Harbor pins
# each task's per-task build context to environment/, which cannot COPY the
# repo). This image is therefore built ONCE from the repo root via
# scripts/harbor/templates/build_image.sh and shared by every sci3d task, which
# reference it by name in task.toml's [verifier.environment].docker_image.
# Build: bash scripts/harbor/templates/build_image.sh
# Default image: suermars/sci3d-scorer:server-20260930-h20
FROM python:3.12-slim

ARG SCI3D_SOURCE_REVISION
LABEL org.opencontainers.image.revision="${SCI3D_SOURCE_REVISION}"

# No apt-get / C toolchain: numpy, scikit-image, nibabel and anthropic all ship
# manylinux wheels for cp312, so pip installs prebuilt binaries. Avoiding apt
# also dodges slow/flaky in-container Debian mirror access on locked-down hosts.

WORKDIR /opt/sci3d

# Copy only what the scorer import path needs. The agent stack (GL/SAM, viewer,
# crawl_data) is deliberately excluded to keep the image small.
COPY pyproject.toml README.md ./
COPY harbor_adapters/ ./harbor_adapters/
COPY sci3d_bench/ ./sci3d_bench/
COPY sci3d_agent/ ./sci3d_agent/

# Scorer-only dependency set. Gemmi canonicalizes crystallographic symbols;
# numpy/scikit-image/nibabel remain available for legacy mask scorers.
#
# Use the Aliyun PyPI mirror: on locked-down hosts the Docker daemon proxy can
# stall on large wheels from files.pythonhosted.org, while *.aliyun.com is in
# the daemon's no_proxy allowlist and resolves directly. Override PIP_INDEX_URL
# at build time (--build-arg) for environments with normal PyPI access.
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com
RUN pip install --no-cache-dir \
        --index-url "${PIP_INDEX_URL}" --trusted-host "${PIP_TRUSTED_HOST}" \
        numpy \
        scipy \
        scikit-image \
        nibabel \
        Pillow \
        httpx \
        'gemmi>=0.7,<0.8' \
        anthropic \
    && pip install --no-cache-dir --no-deps \
        --index-url "${PIP_INDEX_URL}" --trusted-host "${PIP_TRUSTED_HOST}" -e . \
    && python -c 'import anthropic, gemmi, httpx, nibabel, numpy, PIL, scipy, skimage; import sci3d_bench.scorers; from harbor_adapters.sci3d._score_core import score_rewards'

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# The actual scoring entrypoint is mounted at /tests/score.py by Harbor and
# invoked by /tests/test.sh; nothing to CMD here.
