"""Frozen dataclasses for the F-vision session state (Phase 3.1 → 3.3).

This module is the **public API contract** between Phase 3.1 (F-core) and
Phase 3.2 (F-camera / F-render-state) + Phase 3.3 (F-snapshot). Downstream
phases import these types and use ``dataclasses.replace`` for immutable
updates — never mutate fields in place.

Axis-up convention (per SPEC §1 sharp-edge #1)
----------------------------------------------
``Session.axis_up`` is one of {"X", "Y", "Z", "RAS"}:

* ``"Z"``   — ScanNet PLY meshes (indoor scenes); world up is ``(0, 0, 1)``.
* ``"Y"``   — glTF / GLB / OBJ meshes; world up is ``(0, 1, 0)``.
* ``"X"``   — reserved; not used by any current asset kind.
* ``"RAS"`` — NIfTI volumes after LPS→RAS normalization at open time. World
              up is ``(0, 0, 1)`` (RAS Z is superior, i.e. head-up).

Phase 3.2's ``sv3d_camera_set_view`` MUST resolve "front/top/iso" against
this stored ``axis_up``. The convention is: "front" means looking along the
negative direction of the primary horizontal axis with the camera up vector
aligned to ``axis_up``; "top" means looking along the negative ``axis_up``
direction; "iso" is a 45° offset from front + an elevation toward top.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

Mode = Literal[
    "surface",
    "points",
    "volume",
    "mpr_axial",
    "mpr_sagittal",
    "mpr_coronal",
]
Axis = Literal["axial", "coronal", "sagittal"]
AxisUp = Literal["X", "Y", "Z", "RAS"]
Colormap = Literal["red", "jet", "viridis", "glasbey", "label"]
Kind = Literal["mesh", "nifti"]


@dataclass(frozen=True)
class CameraState:
    position: tuple[float, float, float]
    target: tuple[float, float, float]
    up: tuple[float, float, float]
    fov_deg: float


@dataclass(frozen=True)
class MaskState:
    visible: bool
    mask_id: Optional[str]
    colormap: Colormap
    alpha: float


@dataclass(frozen=True)
class SliceState:
    axis: Axis
    index: Optional[int]
    fraction: Optional[float]
    window: tuple[float, float]
    level: float


@dataclass(frozen=True)
class ClipPlaneState:
    enabled: bool
    origin: tuple[float, float, float]
    normal: tuple[float, float, float]
    cap: bool


@dataclass(frozen=True)
class Opacity:
    base: float = 1.0
    mask: float = 0.5
    clip_cap: float = 1.0


@dataclass(frozen=True)
class MaskInfo:
    """Precomputed per-label info (computed at open_scene; O(1) thereafter)."""

    mask_id: str
    voxel_count: int
    centroid_world: tuple[float, float, float]
    bbox_world: tuple[tuple[float, float, float], tuple[float, float, float]]
    best_slices: dict[str, int]  # {"axial": N, "coronal": N, "sagittal": N}


@dataclass(frozen=True)
class Session:
    """Full immutable state of one 3D-vision session.

    Phase 3.2 mutates via ``dataclasses.replace(session, camera=new_camera)``.
    The store then atomically swaps the entry under its lock.
    """

    session_id: str
    kind: Kind
    asset_path: str
    mask_path: Optional[str]
    bbox: tuple[tuple[float, float, float], tuple[float, float, float]]
    axis_up: AxisUp
    affine_4x4: Optional[tuple[tuple[float, ...], ...]]   # nifti only
    masks_info: tuple[MaskInfo, ...]
    camera: CameraState
    mode: Mode
    slice: SliceState
    mask_state: MaskState
    clip_plane: ClipPlaneState
    opacity: Opacity
    created_at: float        # monotonic seconds
    ttl_seconds: int
    rng_seed: int = 0         # reserved for Phase 3.3 deterministic renders
