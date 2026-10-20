"""Input JSON schemas for the F-render-state tools (private module).

Pure data extracted from ``state.py`` to keep that module small. The enum
tuples mirror the ``_types`` Literal definitions and are the single source of
truth for both the schemas here and the validation in ``state.py``.
"""

from __future__ import annotations

from typing import Any

# --- enums (mirror _types Literal definitions) ----------------------------

_MODES: tuple[str, ...] = (
    "surface",
    "points",
    "volume",
    "mpr_axial",
    "mpr_sagittal",
    "mpr_coronal",
)
_MESH_MODES: frozenset[str] = frozenset({"surface", "points"})
_NIFTI_MODES: frozenset[str] = frozenset(
    {"volume", "mpr_axial", "mpr_sagittal", "mpr_coronal"}
)
_AXES: tuple[str, ...] = ("axial", "coronal", "sagittal")
_COLORMAPS: tuple[str, ...] = ("red", "jet", "viridis", "glasbey", "label")
_LAYERS: tuple[str, ...] = ("base", "mask", "clip_cap")
_SHADING: tuple[str, ...] = ("flat", "smooth", "pbr")

_SET_MODE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["session_id", "mode"],
    "properties": {
        "session_id": {"type": "string"},
        "mode": {"enum": list(_MODES)},
        "point_size": {"type": "number", "minimum": 0.0},
        "shading": {"enum": list(_SHADING)},
    },
    "additionalProperties": False,
}

_SET_OPACITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["session_id", "layer", "alpha"],
    "properties": {
        "session_id": {"type": "string"},
        "layer": {"enum": list(_LAYERS)},
        "alpha": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "additionalProperties": False,
}

_TOGGLE_MASK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["session_id"],
    "properties": {
        "session_id": {"type": "string"},
        "visible": {"type": "boolean"},
        "mask_id": {"type": "string"},
        "colormap": {"enum": list(_COLORMAPS)},
        "alpha": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "additionalProperties": False,
}

_SET_SLICE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["session_id", "axis"],
    "properties": {
        "session_id": {"type": "string"},
        "axis": {"enum": list(_AXES)},
        "index": {"type": "integer", "minimum": 0},
        "fraction": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "window": {"type": "number"},
        "level": {"type": "number"},
    },
    "additionalProperties": False,
}

_SET_CLIP_PLANE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["session_id", "enabled"],
    "properties": {
        "session_id": {"type": "string"},
        "enabled": {"type": "boolean"},
        "origin": {
            "type": "array",
            "items": {"type": "number"},
            "minItems": 3,
            "maxItems": 3,
        },
        "normal": {
            "type": "array",
            "items": {"type": "number"},
            "minItems": 3,
            "maxItems": 3,
        },
        "cap": {"type": "boolean"},
    },
    "additionalProperties": False,
}

# Provide exactly one of point_world | mask_id | voxel as the focus point.
# The handler aligns all three MPR planes (axial/coronal/sagittal) to it and
# echoes each plane's resolved index/fraction — a linked-crosshair operation.
_SET_CROSSHAIR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["session_id"],
    "properties": {
        "session_id": {"type": "string"},
        "point_world": {
            "type": "array",
            "items": {"type": "number"},
            "minItems": 3,
            "maxItems": 3,
        },
        "mask_id": {"type": "string"},
        "voxel": {
            "type": "array",
            "items": {"type": "integer", "minimum": 0},
            "minItems": 3,
            "maxItems": 3,
        },
        # When true, also switch render mode to mpr_axial (the active plane).
        "set_mode": {"type": "boolean"},
    },
    "additionalProperties": False,
}
