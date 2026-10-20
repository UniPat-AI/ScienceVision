"""F-vision tools (Phase 3.1 onward)."""
from sci3d_agent.mcp.tools.vision3d.session import TOOLS as _session
from sci3d_agent.mcp.tools.vision3d.camera import TOOLS as _camera
from sci3d_agent.mcp.tools.vision3d.state import TOOLS as _state
from sci3d_agent.mcp.tools.vision3d.snapshot import TOOLS as _snapshot
from sci3d_agent.mcp.tools.vision3d.segment import TOOLS as _segment

TOOLS = [*_session, *_camera, *_state, *_snapshot, *_segment]
