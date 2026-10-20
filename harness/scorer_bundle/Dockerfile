# Docker context is produced only by scorer_bundle/build_context.py. It has no
# agent, MCP server/client, datasets, private masks, or model credentials.
FROM python:3.12-slim

ARG SCI3D_SOURCE_REVISION=unknown
ARG SCI3D_SCORER_MANIFEST_SHA256=unknown
LABEL org.opencontainers.image.title="ScienceVision deterministic scorer" \
      org.opencontainers.image.revision="${SCI3D_SOURCE_REVISION}" \
      io.sciencevision.image.role="formal-scorer" \
      io.sciencevision.scorer.manifest-sha256="${SCI3D_SCORER_MANIFEST_SHA256}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONNOUSERSITE=1 \
    PYTHONPATH=/opt/sci3d-scorer/src \
    HOME=/nonexistent \
    SCI3D_SCORER_NETWORK=disabled

RUN addgroup --system --gid 10001 sci3dscorer \
    && adduser --system --uid 10001 --ingroup sci3dscorer --no-create-home sci3dscorer

WORKDIR /opt/sci3d-scorer
COPY --chown=sci3dscorer:sci3dscorer src/ ./src/
COPY --chown=sci3dscorer:sci3dscorer scorer_cli.py manifest.py offline.py scorer_manifest.json ./
RUN python3 -m py_compile scorer_cli.py manifest.py offline.py \
    && find /opt/sci3d-scorer -type d -exec chmod 0555 {} + \
    && find /opt/sci3d-scorer -type f -exec chmod 0444 {} +

USER 10001:10001
WORKDIR /work
ENTRYPOINT ["python3", "/opt/sci3d-scorer/scorer_cli.py"]
