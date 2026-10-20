"""Generate end-to-end visualization cases for the SAM-as-tool MCP delivery.

Hits the Fake SAM service in-process across all three quality tiers
(perfect/noisy/wrong) and three endpoints (/sam2d, /sam3d_volume,
/sam3d_mesh). For 2D it composites the predicted mask onto the original
image and saves a PNG; for 3D it writes a small Matplotlib figure showing
GT vs predicted voxel/point cloud and the structured metadata returned to
the LLM (the M3-class allowlisted shape).

Output: docs/superpowers/reports/2026-05-28-sam-tool/figs/*.png
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import nibabel as nib
import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

# Make the worktree's package importable when run from elsewhere.
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from scripts.sam_service.fake_server import build_app  # noqa: E402

FIGS = REPO / "docs/superpowers/reports/2026-05-28-sam-tool/figs"
FIGS.mkdir(parents=True, exist_ok=True)

QUALITIES = ("perfect", "noisy", "wrong")
PROMPT = "the target region"


def _png_b64(image: np.ndarray) -> str:
    img = Image.fromarray(image, mode="L" if image.ndim == 2 else "RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _mask_from_b64(b64: str) -> np.ndarray:
    raw = base64.b64decode(b64)
    return np.asarray(Image.open(io.BytesIO(raw)).convert("L"), dtype=np.uint8) > 0


def _voxel_mask_from_b64(b64: str, shape: tuple[int, int, int]) -> np.ndarray:
    raw = base64.b64decode(b64)
    return np.unpackbits(np.frombuffer(raw, dtype=np.uint8))[: int(np.prod(shape))].reshape(shape).astype(bool)


def _per_point_labels_from_b64(b64: str, n: int) -> np.ndarray:
    raw = base64.b64decode(b64)
    return np.unpackbits(np.frombuffer(raw, dtype=np.uint8))[:n].astype(bool)


# ---------- synthetic inputs -------------------------------------------------


def make_2d_image() -> np.ndarray:
    """A 128x128 grayscale image with a bright elliptic blob on a zero background.

    The fake server's 2D GT extractor is `(image > 0)`, so the background must
    stay at 0 to make the blob the GT region. We render a separate "display"
    layer with a faint gradient for human eyeballing only — the network input
    keeps the zero background.
    """
    H = W = 128
    img = np.zeros((H, W), dtype=np.uint8)
    yy, xx = np.mgrid[0:H, 0:W]
    blob = ((xx - 80) / 22) ** 2 + ((yy - 60) / 16) ** 2 < 1.0
    img[blob] = 220
    return img


def make_display_image(img: np.ndarray) -> np.ndarray:
    """Add a faint diagonal gradient on the zero-background pixels for viz only."""
    H, W = img.shape
    yy, xx = np.mgrid[0:H, 0:W]
    gradient = ((xx + yy) // 16).astype(np.uint8)  # 0..15
    out = img.copy()
    bg = img == 0
    out[bg] = gradient[bg] + 20  # 20..35 — visible but well below blob
    return out


def make_volume(path: Path) -> tuple[np.ndarray, tuple[int, int, int]]:
    """A 32x32x32 NIfTI volume with a bright ellipsoid GT region."""
    shape = (32, 32, 32)
    arr = np.full(shape, 50.0, dtype=np.float32)
    zz, yy, xx = np.mgrid[0:32, 0:32, 0:32]
    blob = (((xx - 20) / 6) ** 2 + ((yy - 12) / 4) ** 2 + ((zz - 16) / 5) ** 2) < 1.0
    arr[blob] = 900.0
    img = nib.Nifti1Image(arr, affine=np.eye(4))
    nib.save(img, str(path))
    return arr, shape


def make_ply(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """A simple ASCII PLY: 400 points with brightness varying so half are 'bright'."""
    rng = np.random.default_rng(7)
    n = 400
    xyz = rng.uniform(-1.0, 1.0, size=(n, 3)).astype(np.float32)
    # Bright group is clustered near x>0
    bright = np.where(xyz[:, 0] > 0.0, 220, 40).astype(np.uint8)
    # Add some noise so it isn't perfectly separable
    bright = np.clip(bright + rng.integers(-20, 20, size=n), 0, 255).astype(np.uint8)
    with path.open("w") as f:
        f.write("ply\nformat ascii 1.0\n")
        f.write(f"element vertex {n}\n")
        f.write("property float x\nproperty float y\nproperty float z\n")
        f.write("property uchar red\nproperty uchar green\nproperty uchar blue\n")
        f.write("end_header\n")
        for i in range(n):
            r = g = b = int(bright[i])
            f.write(f"{xyz[i, 0]:.4f} {xyz[i, 1]:.4f} {xyz[i, 2]:.4f} {r} {g} {b}\n")
    return xyz, bright


# ---------- per-endpoint rendering ------------------------------------------


def render_sam2d(client: TestClient, quality: str, image: np.ndarray, display: np.ndarray, out_path: Path):
    payload = {"image_b64": _png_b64(image), "text_prompt": PROMPT}
    r = client.post("/sam2d", json=payload)
    r.raise_for_status()
    data = r.json()
    masks = data["masks"]

    fig, axes = plt.subplots(1, 3, figsize=(11, 4))
    axes[0].imshow(display, cmap="gray", vmin=0, vmax=255)
    axes[0].set_title("Input image\n(blob=GT, dark bg)")
    axes[0].axis("off")

    if masks:
        pred = _mask_from_b64(masks[0]["mask_b64"])
        # The fake server returns the mask same-shape as input (128x128).
        axes[1].imshow(pred, cmap="gray")
        axes[1].set_title(f"Predicted mask\nscore={masks[0]['score']:.2f}")
        axes[1].axis("off")

        rgb = np.stack([display] * 3, axis=-1).astype(np.float32)
        red = np.zeros_like(rgb)
        red[..., 0] = 255
        alpha = 0.55
        m = pred[..., None]
        overlay = np.where(m, rgb * (1 - alpha) + red * alpha, rgb)
        axes[2].imshow(overlay.astype(np.uint8))
        bbox = masks[0]["bbox"]
        axes[2].set_title(f"Overlay (M4)\nbbox={bbox}, area={masks[0]['area']}")
        axes[2].axis("off")
    else:
        for ax in axes[1:]:
            ax.text(0.5, 0.5, "no masks returned", ha="center", va="center")
            ax.axis("off")

    fig.suptitle(f"/sam2d • quality={quality} • text_prompt={PROMPT!r}", fontsize=11)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return data


def render_sam3d_volume(client: TestClient, quality: str, volume: np.ndarray, vol_path: str, out_path: Path):
    payload = {"volume_path": vol_path, "text_prompt": PROMPT}
    r = client.post("/sam3d_volume", json=payload)
    r.raise_for_status()
    data = r.json()
    masks = data["masks"]

    fig = plt.figure(figsize=(12, 4))
    # GT slice
    ax1 = fig.add_subplot(1, 3, 1)
    mid = volume.shape[2] // 2
    ax1.imshow(volume[:, :, mid], cmap="gray")
    ax1.set_title(f"NIfTI input (z={mid})")
    ax1.axis("off")

    if masks:
        m = masks[0]
        # The fake server's voxel_mask_b64 is packed bits in (z,y,x)? — let's
        # just take the centroid + bbox and visualize structurally without
        # decoding the voxel bytes (the LLM never sees them either; this is
        # exactly the M3 contract).
        ax2 = fig.add_subplot(1, 3, 2, projection="3d")
        bbox = np.array(m["bbox"])
        c = m["centroid"]
        # Draw bbox edges
        x0, y0, z0 = bbox[0]
        x1, y1, z1 = bbox[1]
        edges = [
            ([x0, x1], [y0, y0], [z0, z0]),
            ([x0, x1], [y1, y1], [z0, z0]),
            ([x0, x1], [y0, y0], [z1, z1]),
            ([x0, x1], [y1, y1], [z1, z1]),
            ([x0, x0], [y0, y1], [z0, z0]),
            ([x1, x1], [y0, y1], [z0, z0]),
            ([x0, x0], [y0, y1], [z1, z1]),
            ([x1, x1], [y0, y1], [z1, z1]),
            ([x0, x0], [y0, y0], [z0, z1]),
            ([x1, x1], [y0, y0], [z0, z1]),
            ([x0, x0], [y1, y1], [z0, z1]),
            ([x1, x1], [y1, y1], [z0, z1]),
        ]
        for ex, ey, ez in edges:
            ax2.plot(ex, ey, ez, color="tab:red", lw=1.5)
        ax2.scatter([c[0]], [c[1]], [c[2]], color="tab:red", s=60, label="centroid")
        ax2.set_xlim(0, volume.shape[0])
        ax2.set_ylim(0, volume.shape[1])
        ax2.set_zlim(0, volume.shape[2])
        ax2.set_title(f"Predicted bbox + centroid\nscore={m['score']:.2f}, voxels={m['voxel_count']}")
        ax2.legend(loc="upper left", fontsize=8)

        # Structured JSON returned to LLM (M3 contract: no pixels!)
        ax3 = fig.add_subplot(1, 3, 3)
        ax3.axis("off")
        # The LLM-visible shape is the handler's allowlisted projection.
        llm_view = {
            "label": PROMPT,
            "score": round(m["score"], 3),
            "voxel_count": m["voxel_count"],
            "centroid_world": [round(v, 2) for v in m["centroid"]],
            "bbox_world": [[round(v, 2) for v in p] for p in m["bbox"]],
        }
        ax3.text(0.0, 1.0, "Returned to LLM (M3 — no pixels):", fontsize=9, weight="bold", va="top")
        ax3.text(0.0, 0.85, json.dumps(llm_view, indent=2), family="monospace", fontsize=8, va="top")
    fig.suptitle(f"/sam3d_volume • quality={quality}", fontsize=11)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return data


def render_sam3d_mesh(client: TestClient, quality: str, xyz: np.ndarray, brightness: np.ndarray, ply_path: str, out_path: Path):
    payload = {"pointcloud_path": ply_path, "text_prompt": PROMPT}
    r = client.post("/sam3d_mesh", json=payload)
    r.raise_for_status()
    data = r.json()
    segs = data["segments"]

    fig = plt.figure(figsize=(12, 4))
    # GT: color points by brightness
    ax1 = fig.add_subplot(1, 3, 1, projection="3d")
    ax1.scatter(xyz[:, 0], xyz[:, 1], xyz[:, 2], c=brightness, cmap="gray", s=4)
    ax1.set_title("PLY input (colored by brightness)")

    if segs:
        s = segs[0]
        labels = _per_point_labels_from_b64(s["per_point_labels_b64"], xyz.shape[0])
        ax2 = fig.add_subplot(1, 3, 2, projection="3d")
        ax2.scatter(xyz[~labels, 0], xyz[~labels, 1], xyz[~labels, 2], c="lightgray", s=4, label="not selected")
        ax2.scatter(xyz[labels, 0], xyz[labels, 1], xyz[labels, 2], c="tab:red", s=8, label="predicted")
        ax2.set_title(f"Predicted segment\nscore={s['score']:.2f}, points={s['point_count']}")
        ax2.legend(loc="upper left", fontsize=8)

        ax3 = fig.add_subplot(1, 3, 3)
        ax3.axis("off")
        llm_view = {
            "label": PROMPT,
            "score": round(s["score"], 3),
            "point_count": s["point_count"],
            "centroid_world": [round(v, 2) for v in s["centroid"]],
            "bbox_world": [[round(v, 2) for v in p] for p in s["bbox"]],
        }
        ax3.text(0.0, 1.0, "Returned to LLM (M3 — no per-point bytes):", fontsize=9, weight="bold", va="top")
        ax3.text(0.0, 0.85, json.dumps(llm_view, indent=2), family="monospace", fontsize=8, va="top")
    fig.suptitle(f"/sam3d_mesh • quality={quality}", fontsize=11)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return data


# ---------- main -------------------------------------------------------------


def main() -> None:
    image = make_2d_image()
    display = make_display_image(image)

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        vol_path = td_path / "vol.nii.gz"
        ply_path = td_path / "cloud.ply"
        volume, _ = make_volume(vol_path)
        xyz, brightness = make_ply(ply_path)

        records: dict[str, dict] = {}
        for quality in QUALITIES:
            app = build_app(quality=quality)
            with TestClient(app) as client:
                health = client.get("/healthz").json()
                print(f"[{quality}] /healthz -> {health}")

                d2 = render_sam2d(
                    client, quality, image, display, FIGS / f"sam2d_{quality}.png"
                )
                d3v = render_sam3d_volume(
                    client, quality, volume, str(vol_path), FIGS / f"sam3d_volume_{quality}.png"
                )
                d3m = render_sam3d_mesh(
                    client, quality, xyz, brightness, str(ply_path), FIGS / f"sam3d_mesh_{quality}.png"
                )
                records[quality] = {
                    "sam2d": _summarize_2d(d2),
                    "sam3d_volume": _summarize_3d_volume(d3v),
                    "sam3d_mesh": _summarize_3d_mesh(d3m),
                }

        out_json = FIGS.parent / "results.json"
        out_json.write_text(json.dumps(records, indent=2))
        print(f"wrote {out_json}")


def _summarize_2d(data: dict) -> dict:
    if not data["masks"]:
        return {"masks": 0}
    m = data["masks"][0]
    return {
        "masks": len(data["masks"]),
        "score": round(m["score"], 3),
        "bbox": m["bbox"],
        "area": m["area"],
    }


def _summarize_3d_volume(data: dict) -> dict:
    if not data["masks"]:
        return {"masks": 0}
    m = data["masks"][0]
    return {
        "masks": len(data["masks"]),
        "score": round(m["score"], 3),
        "voxel_count": m["voxel_count"],
        "centroid": [round(v, 2) for v in m["centroid"]],
        "bbox": [[round(v, 2) for v in p] for p in m["bbox"]],
    }


def _summarize_3d_mesh(data: dict) -> dict:
    if not data["segments"]:
        return {"segments": 0}
    s = data["segments"][0]
    return {
        "segments": len(data["segments"]),
        "score": round(s["score"], 3),
        "point_count": s["point_count"],
        "centroid": [round(v, 2) for v in s["centroid"]],
        "bbox": [[round(v, 2) for v in p] for p in s["bbox"]],
    }


if __name__ == "__main__":
    main()
