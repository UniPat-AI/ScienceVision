"""Viewer-frame canonical view angles + the Y-up -> Z-up renderer transform.

The values are viewer-frame (Y-up) spherical degrees — the frame
``sv3d_render_canonical_views`` samples and ``scripts/harbor/view_protocol.py``
plans against. A test holds this table equal to the planner's copy and to the
directions ``vision3d._snapshot_camera._canonical_offset("Y", view)`` derives.

``YUP_TO_ZUP`` is a proper rotation (det = +1): ``(x, y, z) -> (x, -z, y)``. It
must never be replaced by a mirror — a mirrored molecule still renders
plausibly while flipping every chirality-sensitive answer. matplotlib's
``view_init`` is Z-up, so a viewer-frame azimuth ``a`` renders at ``a - 90``.
"""
from __future__ import annotations

import numpy as np

VIEWER_ANGLES: dict[str, tuple[float, float]] = {
    "front": (0.0, 0.0),
    "right": (90.0, 0.0),
    "back": (180.0, 0.0),
    "left": (270.0, 0.0),
    "top": (0.0, 90.0),
    "bottom": (0.0, -90.0),
    "iso": (45.0, 35.26439),
}
YUP_TO_ZUP = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]])


def rotate_yup_to_zup(coords: np.ndarray) -> np.ndarray:
    """``coords`` (N x 3, viewer Y-up) expressed in the renderer's Z-up frame."""
    return np.asarray(coords, dtype=np.float64) @ YUP_TO_ZUP.T


def to_matplotlib_frame(azimuth: float, elevation: float) -> tuple[float, float]:
    """The matplotlib ``view_init`` angles reproducing a viewer-frame direction.

    Valid only against coordinates already passed through `rotate_yup_to_zup`.
    """
    return (round(float(azimuth) - 90.0, 6), round(float(elevation), 6))
