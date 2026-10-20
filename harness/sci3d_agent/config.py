"""Tool schemas exposed to the LLM. Same JSON-schema shape as Anthropic tool-use."""

EXECUTE_CODE_TOOL = {
    "name": "execute_code",
    "description": "Run a Python snippet in the persistent Jupyter kernel. State persists across calls.",
    "input_schema": {
        "type": "object",
        "properties": {"code": {"type": "string"}},
        "required": ["code"],
    },
}

FINISH_TOOL = {
    "name": "finish",
    "description": "Submit the final answer and end the task. The 'answer' field shape must match the task's answer_schema.",
    "input_schema": {
        "type": "object",
        "properties": {
            # `answer` accepts any JSON value (V2=string, V4=object,
            # V6=number). Strict tool-schema validators (Anthropic /
            # Bedrock via Mindra proxy) reject an empty `{}` schema —
            # the anyOf shape is the spelling they accept.
            "answer": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "object"},
                    {"type": "number"},
                    {"type": "integer"},
                    {"type": "boolean"},
                    {"type": "array"},
                    {"type": "null"},
                ]
            }
        },
        "required": ["answer"],
    },
}

RENDER_VIEW_TOOL = {
    "name": "render_view",
    "description": "Render the loaded 3D asset from a specified camera viewpoint via the viewer-bridge. Returns PNG bytes.",
    "input_schema": {
        "type": "object",
        "properties": {
            "azimuth": {"type": "number"},
            "elevation": {"type": "number"},
            "roll": {"type": "number"},
            "distance": {"type": "number"},
        },
        "required": ["azimuth", "elevation"],
    },
}

SCREENSHOT_TOOL = {
    "name": "screenshot",
    "description": "Capture the current viewer state as PNG.",
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

SET_CAMERA_TOOL = {
    "name": "set_camera",
    "description": "Mutate the viewer camera without returning an image.",
    "input_schema": {
        "type": "object",
        "properties": {
            "azimuth": {"type": "number"},
            "elevation": {"type": "number"},
            "roll": {"type": "number"},
            "distance": {"type": "number"},
        },
        "required": [],
    },
}

SYSTEM_PROMPT_MODE1 = (
    "You are a scientific 3D analysis agent. You can call execute_code "
    "to run Python in a persistent Jupyter kernel and finish to submit your answer. "
    "You can read 3D source bytes (e.g. trimesh.load, nibabel.load) but MUST NOT "
    "render, screenshot, or otherwise produce visual output. If you generate an image "
    "it will be discarded and flagged as a violation."
)

SYSTEM_PROMPT_MODE2 = (
    "You are a scientific 3D analysis agent. The user has provided ONE prerendered "
    "image of the asset alongside the 3D source bytes. You can call execute_code "
    "to run Python and finish to submit your answer. You MUST NOT render additional "
    "views or screenshots — any kernel-produced image will be discarded and flagged."
)

SYSTEM_PROMPT_MODE3 = (
    "You are a scientific 3D analysis agent. You have full visual access: call "
    "execute_code for in-kernel rendering, render_view / screenshot / set_camera "
    "for the dedicated 3D viewer, and finish to submit your answer."
)

SAM_SERVICE_URL_DEFAULT = "http://localhost:8900"


SV3D_SEGMENT_2D_TOOL = {
    "name": "sv3d_segment_2d",
    "description": (
        "Render the current 3D session at the requested canonical view and "
        "ask the SAM service for a 2D mask matching `text_prompt`. Returns the "
        "rendered image (image_png_base64), the segmentation masks (each with "
        "mask_b64, score, bbox_2d, area_px), and the view used."
    ),
    "input_schema": {
        "type": "object",
        "required": ["session_id", "text_prompt"],
        "properties": {
            "session_id": {"type": "string"},
            "view": {
                "enum": ["front", "back", "left", "right", "top", "bottom", "iso"],
                "default": "iso",
            },
            "text_prompt": {"type": "string", "minLength": 1},
        },
        "additionalProperties": False,
    },
}


SV3D_SEGMENT_3D_VOLUME_TOOL = {
    "name": "sv3d_segment_3d_volume",
    "description": (
        "Ask the SAM service to segment a volumetric medical scan (the "
        "current nifti session) matching `text_prompt`. Returns ONLY "
        "structured summary data — voxel_count, centroid_world, bbox_world "
        "— no pixels and no raw voxel mask. Use this to localise a concept "
        "in 3D source data without ever rendering an image."
    ),
    "input_schema": {
        "type": "object",
        "required": ["session_id", "text_prompt"],
        "properties": {
            "session_id": {"type": "string"},
            "text_prompt": {"type": "string", "minLength": 1},
        },
        "additionalProperties": False,
    },
}


SV3D_SEGMENT_3D_MESH_TOOL = {
    "name": "sv3d_segment_3d_mesh",
    "description": (
        "Ask the SAM service to segment a 3D mesh / point cloud (the current "
        "mesh session) matching `text_prompt`. Returns ONLY structured "
        "summary data — point_count, centroid_world, bbox_world — no pixels "
        "and no raw per-point label array."
    ),
    "input_schema": {
        "type": "object",
        "required": ["session_id", "text_prompt"],
        "properties": {
            "session_id": {"type": "string"},
            "text_prompt": {"type": "string", "minLength": 1},
        },
        "additionalProperties": False,
    },
}


SYSTEM_PROMPT_MODE1_SEG3D_VOL = (
    "You are a scientific 3D analysis agent. You have access to "
    "`execute_code`, `finish`, and `sv3d_segment_3d_volume`. The "
    "segmentation tool returns ONLY structured summary data about "
    "concepts you ask it to find inside a CT volume (centroid, bounding "
    "box, voxel count) — never any image or pixel data. Use it together "
    "with `execute_code` to reason about 3D geometry. Submit your final "
    "answer via the `finish` tool."
)

SYSTEM_PROMPT_MODE1_SEG3D_MESH = (
    "You are a scientific 3D analysis agent. You have access to "
    "`execute_code`, `finish`, and `sv3d_segment_3d_mesh`. The "
    "segmentation tool returns ONLY structured summary data about "
    "concepts you ask it to find inside a 3D scene (centroid, bounding "
    "box, point count) — never any image or pixel data. Use it together "
    "with `execute_code` to reason about 3D geometry. Submit your final "
    "answer via the `finish` tool."
)

SYSTEM_PROMPT_MODE3_SEG2D = (
    "You are a scientific 3D analysis agent. In addition to the "
    "render/screenshot/set_camera tools, you have `sv3d_segment_2d` "
    "which produces a 2D mask overlay on a rendered view for a "
    "text-described concept. Use the rendered images (including the "
    "mask overlays) to reason about the scene. Submit your final answer "
    "via the `finish` tool."
)

SYSTEM_PROMPT_MODE4_MEDICAL_MPR = (
    "You are a medical 3D imaging analysis agent working with a CT/MRI "
    "volume, just as a radiologist would in a multiplanar (MPR) viewer. "
    "Start a session with `sv3d_open_scene`, then explore the volume: "
    "`sv3d_set_render_mode` switches between volume rendering and the "
    "mpr_axial / mpr_sagittal / mpr_coronal planes; `sv3d_set_slice` scrubs "
    "to a specific slice (with window/level); `sv3d_set_crosshair` aligns "
    "all three planes to one focus point (a world point, a voxel, or a "
    "segmented mask's location) so you can read the three correlated views "
    "at once; `sv3d_segment_3d_volume` localises a named structure; and "
    "`sv3d_render` / `sv3d_render_canonical_views` produce images to look "
    "at. Inspect the actual slices before answering — do not rely on a "
    "generic anatomical prior. Pay attention to laterality (left/right). "
    "Use `execute_code` for any numeric reasoning. Submit your final answer "
    "via the `finish` tool."
)

# Tools advertised by mode4_medical_mpr — a curated subset of the MCP
# toolkit covering session lifecycle, MPR navigation, the linked crosshair,
# 3D volume segmentation, and rendering. `finish` / `execute_code` are added
# by the mode profile separately.
MODE4_MEDICAL_MPR_TOOL_NAMES = (
    "sv3d_open_scene",
    "sv3d_close_scene",
    "sv3d_get_state",
    "sv3d_set_render_mode",
    "sv3d_set_slice",
    "sv3d_set_crosshair",
    "sv3d_set_clip_plane",
    "sv3d_set_opacity",
    "sv3d_toggle_mask",
    "sv3d_segment_3d_volume",
    "sv3d_render",
    "sv3d_render_canonical_views",
)
