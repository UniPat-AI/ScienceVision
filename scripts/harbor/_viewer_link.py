"""Parse a ScienceVision viewer URL into asset + optional camera pose.

Only the gltf viewer carries a camera (``?cam=base64url(JSON …)``, the inverse
of scripts/eval/sv3d_emit_viewer_urls.py::encode_cam); niivue/pointcloud/
cornerstone/molstar links carry only ``file``.

``decode_cam`` mirrors ``viewer/shared/cam_url.js::decodeCam``: base64url
(``-``/``_`` alphabet) with ``=`` padding stripped by the encoder and
re-added here before decoding.
"""
from __future__ import annotations

import base64
import json
import math
from urllib.parse import urlparse, parse_qs


def decode_cam(s: "str | None") -> "dict | None":
    """Inverse of ``sv3d_emit_viewer_urls.encode_cam``.

    Returns the decoded ``{position, target, up?, fov_deg?}`` dict, or
    ``None`` on any malformed/invalid input. Never raises.
    """
    if not s:
        return None
    try:
        pad = s + "=" * (-len(s) % 4)
        obj = json.loads(base64.urlsafe_b64decode(pad.encode("ascii")).decode("utf-8"))
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    position = obj.get("position")
    target = obj.get("target")
    if not (isinstance(position, list) and isinstance(target, list)):
        return None
    if len(position) != 3 or len(target) != 3:
        return None
    return obj


def _decode_slice(s: "str | None") -> "dict | None":
    """Inverse of ``sv3d_emit_viewer_urls.encode_slice`` /
    ``viewer/shared/cam_url.js::decodeSlice``: ``axis:fraction:window:level``.

    Only ``axis`` is guaranteed present; the remaining fields are optional
    and parsed leniently (matching the JS decoder's ``undefined`` handling).
    """
    if not s:
        return None
    parts = s.split(":")
    axis = parts[0]
    if axis not in ("axial", "sagittal", "coronal"):
        return None
    out: dict = {"axis": axis}
    for key, idx in (("fraction", 1), ("window", 2), ("level", 3)):
        if len(parts) > idx and parts[idx] != "":
            try:
                out[key] = float(parts[idx])
            except ValueError:
                pass
    return out


def parse_viewer_link(url: str) -> dict:
    """Parse a viewer URL into ``{viewer_type, file, cam, slice}``.

    ``viewer_type`` is the html page stem (e.g. ``gltf``, ``niivue``,
    ``pointcloud``). ``cam``/``slice`` are populated only when
    ``viewer_type == "gltf"``; other viewers carry asset-only links.
    Malformed input never raises: missing/invalid pieces yield ``None`` or
    empty-string fields.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return {"viewer_type": "", "file": "", "cam": None, "slice": None}

    path = parsed.path or ""
    stem = path.rsplit("/", 1)[-1]
    if stem.endswith(".html"):
        stem = stem[: -len(".html")]

    q = parse_qs(parsed.query)
    file = (q.get("file") or [""])[0]

    cam = None
    sl = None
    if stem == "gltf":
        cam = decode_cam((q.get("cam") or [None])[0])
        sl = _decode_slice((q.get("slice") or [None])[0])

    return {"viewer_type": stem, "file": file, "cam": cam, "slice": sl}


def seed_pose_from_cam(cam: "dict | None") -> "dict | None":
    """Derive a mesh orbit seed ``{azimuth, elevation}`` (degrees) from ``cam``.

    Uses the eye->center direction ``normalize(position - target)`` under the
    Z-up convention shared with ``mesh_orbit_azimuths``:
    ``azimuth = atan2(dy, dx)``, ``elevation = asin(clamp(dz, -1, 1))``.
    Returns ``None`` if ``cam`` is falsy.
    """
    if not cam:
        return None
    try:
        position = cam["position"]
        target = cam["target"]
        d = [float(position[i]) - float(target[i]) for i in range(3)]
    except (KeyError, IndexError, TypeError, ValueError):
        return None

    norm = math.sqrt(sum(c * c for c in d)) or 1.0
    dx, dy, dz = (c / norm for c in d)
    azimuth = math.degrees(math.atan2(dy, dx))
    elevation = math.degrees(math.asin(max(-1.0, min(1.0, dz))))
    return {"azimuth": round(azimuth, 6), "elevation": round(elevation, 6)}
