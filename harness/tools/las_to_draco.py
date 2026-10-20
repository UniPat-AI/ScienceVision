#!/usr/bin/env python3
"""Re-encode bridge-LiDAR .las point clouds to Draco .drc for fast web loading.

Env (uv is the only working Python packaging path on this box):
    uv venv --python 3.12 /tmp/dracoenv
    /tmp/dracoenv/bin/uv pip install DracoPy==2.0.0 laspy==2.7.0 numpy
Run:
    /tmp/dracoenv/bin/python tools/las_to_draco.py            # encode the 6 default clouds
    /tmp/dracoenv/bin/python tools/las_to_draco.py --self-test
    /tmp/dracoenv/bin/python tools/las_to_draco.py a.las b.las # encode specific files

Classification is packed into color.R (0-31, exact in Draco 8-bit color).
Positions are centered by per-axis bbox midpoint (min+max)/2 to MATCH
viewer/pointcloud.html's parseLAS centering, so the camera auto-fit is unchanged.
Intensity and RGB are all-zero in these clouds and are intentionally dropped.
"""
import glob
import os
import sys

import numpy as np
import DracoPy

DEFAULT_GLOB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "crawl_data", "architecture", "bridge_pcl", "*-Bridge.las",
)


def las_arrays(path):
    """Load a .las → (pts_f32 [N,3] centered by bbox midpoint, cls_u8 [N])."""
    import laspy
    las = laspy.read(path)
    x = np.asarray(las.x, dtype=np.float64)
    y = np.asarray(las.y, dtype=np.float64)
    z = np.asarray(las.z, dtype=np.float64)
    cx = (x.min() + x.max()) / 2
    cy = (y.min() + y.max()) / 2
    cz = (z.min() + z.max()) / 2
    pts = np.column_stack([x - cx, y - cy, z - cz]).astype(np.float32)
    cls = (np.asarray(las.classification) & 0x1f).astype(np.uint8)
    return pts, cls


def encode_cloud(pts_f32, cls_u8, *, quantization_bits=14, compression_level=7):
    """Draco-encode points with classification packed into color.R."""
    n = pts_f32.shape[0]
    colors = np.zeros((n, 3), dtype=np.uint8)
    colors[:, 0] = cls_u8
    return DracoPy.encode(
        pts_f32, colors=colors,
        quantization_bits=quantization_bits,
        compression_level=compression_level,
    )


def decode_check(drc_bytes):
    """Decode .drc → (pts [M,3] float32, cls [M] uint8 recovered from color.R)."""
    m = DracoPy.decode(drc_bytes)
    pts = np.asarray(m.points, dtype=np.float32)
    colors = np.asarray(m.colors)
    # DracoPy returns colors as uint8 0-255; color.R holds the class id directly.
    cls = np.rint(colors[:, 0]).astype(np.uint8) if colors.size else np.zeros(pts.shape[0], np.uint8)
    return pts, cls


def encode_file(path, quantization_bits=14, compression_level=7):
    """Encode one .las → sibling .drc. Returns (out_path, src_pts, out_pts, merged, out_bytes)."""
    pts, cls = las_arrays(path)
    buf = encode_cloud(pts, cls, quantization_bits=quantization_bits,
                        compression_level=compression_level)
    out_path = os.path.splitext(path)[0] + ".drc"
    with open(out_path, "wb") as f:
        f.write(buf)
    dpts, _ = decode_check(buf)
    return out_path, pts.shape[0], dpts.shape[0], pts.shape[0] - dpts.shape[0], len(buf)


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    if "--self-test" in argv:
        return _self_test()
    files = args if args else sorted(glob.glob(DEFAULT_GLOB))
    if not files:
        print("no .las files found (glob: %s)" % DEFAULT_GLOB, file=sys.stderr)
        return 1
    print("%-22s %10s %10s %7s %8s" % ("file", "src_pts", "out_pts", "merged", "out_MB"))
    for p in files:
        out, src, out_pts, merged, nbytes = encode_file(p)
        print("%-22s %10d %10d %7d %8.2f" %
              (os.path.basename(out), src, out_pts, merged, nbytes / 1e6))
    return 0


def _self_test() -> int:
    import numpy as np
    # 1000 points on a deterministic lattice in a ~50-unit cube, offset far from
    # origin (mimics UTM), with 5 classes cycled + a couple of exact duplicates.
    n = 1000
    g = np.linspace(0.0, 50.0, n, dtype=np.float64)
    xs = 445000.0 + g
    ys = 3766000.0 + (g * 0.5)
    zs = 12.0 + np.sin(g) * 3.0
    raw_cls = (np.arange(n) % 5).astype(np.uint8)
    # inject 2 exact-duplicate points (same coords) — Draco will merge these
    xs[10] = xs[11]; ys[10] = ys[11]; zs[10] = zs[11]; raw_cls[10] = raw_cls[11]

    cx = (xs.min() + xs.max()) / 2
    cy = (ys.min() + ys.max()) / 2
    cz = (zs.min() + zs.max()) / 2
    pts = np.column_stack([xs - cx, ys - cy, zs - cz]).astype(np.float32)
    cls = raw_cls & 0x1f

    buf = encode_cloud(pts, cls, quantization_bits=14)
    dpts, dcls = decode_check(buf)

    # (a) point count: source minus exact dups (Draco merges the 1 coincident pair)
    assert dpts.shape[0] in (n, n - 1), f"unexpected point count {dpts.shape[0]}"
    # (b) class histogram preserved (allowing the one merged dup)
    src_hist = np.bincount(cls, minlength=32)
    out_hist = np.bincount(dcls, minlength=32)
    assert np.abs(src_hist - out_hist).sum() <= 1, f"class histogram drift {src_hist} vs {out_hist}"
    # (c) spatial fidelity: every decoded point within one quantization step of
    #     a source point. step = extent / 2^14.
    extent = float(np.max(pts.max(0) - pts.min(0)))
    step = extent / (2 ** 14)
    # nearest-source distance for a sample of decoded points
    import numpy as _np
    idx = _np.linspace(0, dpts.shape[0] - 1, 50).astype(int)
    for i in idx:
        d = _np.linalg.norm(pts - dpts[i], axis=1).min()
        assert d <= step * 2, f"point {i} off by {d} > 2*step {step}"
    print("self-test OK: pts=%d hist_drift=%d step=%.4g" %
          (dpts.shape[0], int(_np.abs(src_hist - out_hist).sum()), step))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
