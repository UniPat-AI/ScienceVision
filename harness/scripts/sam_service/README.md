# Fake SAM service

Standalone FastAPI server that mimics the future real SAM service on H20
(10.0.0.132). Returns synthetic masks derived from ground-truth, so the
ablation scaffold can be developed before real SAM weights are deployed.

## Launch

    pip install -e ".[mcp,sam-service]"
    FAKE_SAM_QUALITY=noisy python -m scripts.sam_service.fake_server
    # Listens on http://localhost:8900

## Quality tiers (env `FAKE_SAM_QUALITY`)

- `perfect` — GT mask returned unchanged (IoU = 1.0)
- `noisy` (default) — GT ± erosion/dilation/dropout (IoU ∈ [0.6, 0.9])
- `wrong` — random region unrelated to prompt (IoU ≈ 0)

## Endpoints

- `POST /sam2d` — 2D mask given image + text prompt
- `POST /sam3d_volume` — voxel mask given volume path + text prompt
- `POST /sam3d_mesh` — per-point labels given pointcloud path + text prompt
- `GET /healthz` — liveness

See `docs/superpowers/specs/2026-05-27-sam-tool-ablation-design.md` §5
for the API contract.
