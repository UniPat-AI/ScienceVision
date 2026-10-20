"""Frozen ModeProfile dataclasses encapsulating mode-specific behavior."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal
from sci3d_agent.config import (
    EXECUTE_CODE_TOOL, FINISH_TOOL, RENDER_VIEW_TOOL, SCREENSHOT_TOOL, SET_CAMERA_TOOL,
    SV3D_SEGMENT_2D_TOOL, SV3D_SEGMENT_3D_VOLUME_TOOL, SV3D_SEGMENT_3D_MESH_TOOL,
    SYSTEM_PROMPT_MODE1, SYSTEM_PROMPT_MODE2, SYSTEM_PROMPT_MODE3,
    SYSTEM_PROMPT_MODE1_SEG3D_VOL, SYSTEM_PROMPT_MODE1_SEG3D_MESH,
    SYSTEM_PROMPT_MODE3_SEG2D, SYSTEM_PROMPT_MODE4_MEDICAL_MPR,
    MODE4_MEDICAL_MPR_TOOL_NAMES,
)

ModeName = Literal[
    "mode1_no_vision",
    "mode1_seg3d_volume",
    "mode1_seg3d_mesh",
    "mode2_one_image",
    "mode3_free_visual",
    "mode3_seg2d",
    "mode4_mcp",
    "mode4_medical_mpr",
]
RenderPolicy = Literal["blocked", "allowed_no_pixels_returned", "allowed"]
MODE_NAMES = (
    "mode1_no_vision",
    "mode1_seg3d_volume",
    "mode1_seg3d_mesh",
    "mode2_one_image",
    "mode3_free_visual",
    "mode3_seg2d",
    "mode4_mcp",
    "mode4_medical_mpr",
)

SYSTEM_PROMPT_MODE4 = (
    "You are a scientific 3D analysis agent with access to the ScienceVision "
    "MCP toolkit (56 tools across 6 categories: catalog, URL builders, "
    "asset inspectors, static renderers, pipeline actions, and stateful "
    "3D-vision sessions). Use sv_search_matched_qa to find QA records, "
    "sv_inspect_* to read metadata, sv3d_open_scene to start a 3D session, "
    "and the sv3d_camera_* / sv3d_set_* / sv3d_render tools to explore "
    "an asset just as a human would in the browser viewer. "
    "Submit your final answer via the `finish` tool."
)


@dataclass(frozen=True)
class ModeProfile:
    name: ModeName
    system_prompt: str
    advertised_tools: tuple
    render_policy: RenderPolicy


def _build_mode4_tools() -> tuple:
    """Lazy: import REGISTRY at call time so tests can register fakes first."""
    from sci3d_agent.mcp_bridge import list_mcp_tool_schemas
    return (FINISH_TOOL, *list_mcp_tool_schemas())


def _build_medical_mpr_tools() -> tuple:
    """Lazy: curated MCP subset for the medical MPR mode + execute_code/finish."""
    from sci3d_agent.mcp_bridge import list_mcp_tool_schemas
    return (
        EXECUTE_CODE_TOOL,
        FINISH_TOOL,
        *list_mcp_tool_schemas(MODE4_MEDICAL_MPR_TOOL_NAMES),
    )


_PROFILES = {
    "mode1_no_vision": ModeProfile(
        name="mode1_no_vision",
        system_prompt=SYSTEM_PROMPT_MODE1,
        advertised_tools=(EXECUTE_CODE_TOOL, FINISH_TOOL),
        render_policy="blocked",
    ),
    "mode2_one_image": ModeProfile(
        name="mode2_one_image",
        system_prompt=SYSTEM_PROMPT_MODE2,
        advertised_tools=(EXECUTE_CODE_TOOL, FINISH_TOOL),
        render_policy="allowed_no_pixels_returned",
    ),
    "mode3_free_visual": ModeProfile(
        name="mode3_free_visual",
        system_prompt=SYSTEM_PROMPT_MODE3,
        advertised_tools=(EXECUTE_CODE_TOOL, FINISH_TOOL, RENDER_VIEW_TOOL, SCREENSHOT_TOOL, SET_CAMERA_TOOL),
        render_policy="allowed",
    ),
    "mode1_seg3d_volume": ModeProfile(
        name="mode1_seg3d_volume",
        system_prompt=SYSTEM_PROMPT_MODE1_SEG3D_VOL,
        advertised_tools=(EXECUTE_CODE_TOOL, FINISH_TOOL, SV3D_SEGMENT_3D_VOLUME_TOOL),
        render_policy="blocked",
    ),
    "mode1_seg3d_mesh": ModeProfile(
        name="mode1_seg3d_mesh",
        system_prompt=SYSTEM_PROMPT_MODE1_SEG3D_MESH,
        advertised_tools=(EXECUTE_CODE_TOOL, FINISH_TOOL, SV3D_SEGMENT_3D_MESH_TOOL),
        render_policy="blocked",
    ),
    "mode3_seg2d": ModeProfile(
        name="mode3_seg2d",
        system_prompt=SYSTEM_PROMPT_MODE3_SEG2D,
        advertised_tools=(
            EXECUTE_CODE_TOOL,
            FINISH_TOOL,
            RENDER_VIEW_TOOL,
            SCREENSHOT_TOOL,
            SET_CAMERA_TOOL,
            SV3D_SEGMENT_2D_TOOL,
        ),
        render_policy="allowed",
    ),
}


def get_profile(name: str) -> ModeProfile:
    if name == "mode4_mcp":
        # Built lazily so test registry mutations and additional tool
        # registrations are picked up at call time.
        return ModeProfile(
            name="mode4_mcp",
            system_prompt=SYSTEM_PROMPT_MODE4,
            advertised_tools=_build_mode4_tools(),
            render_policy="allowed",
        )
    if name == "mode4_medical_mpr":
        # Lazy for the same reason as mode4_mcp: pick up tool registrations
        # (e.g. sv3d_set_crosshair) at call time.
        return ModeProfile(
            name="mode4_medical_mpr",
            system_prompt=SYSTEM_PROMPT_MODE4_MEDICAL_MPR,
            advertised_tools=_build_medical_mpr_tools(),
            render_policy="allowed",
        )
    return _PROFILES[name]
