from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.harbor import view_protocol as VP  # noqa: E402


def _named(entries):
    return [v for kind, v in entries if kind == "named"]


def test_protocol_is_nested_1_3_6_12():
    sets = {n: set(_named(VP.entries(n))) for n in (1, 3, 6, 12)}
    assert sets[1] < sets[3] < sets[6] < sets[12]
    assert sets[6] == set(VP.NAMED_6)
    assert len(VP.entries(12)) == 12


def test_cc2d6_matches_the_ccmcp_canonical_default():
    # Set equality, not tuple equality: ccmcp's default is an argument default
    # (order is not semantically binding there), while cc2d's order fixes which
    # view lands in view_00..05. The frozen requirement is the same SET.
    pytest.importorskip("sci3d_agent.mcp.tools.vision3d.snapshot")
    from sci3d_agent.mcp.tools.vision3d.snapshot import _CANONICAL_VIEWS_DEFAULT

    assert set(_named(VP.entries(6))) == set(_CANONICAL_VIEWS_DEFAULT)
    assert _named(VP.entries(6))[0] == "iso"


def test_named_angles_agree_with_the_viewer_sampling_directions():
    pytest.importorskip("sci3d_agent.mcp.tools.vision3d._snapshot_camera")
    from sci3d_agent.mcp.tools.vision3d._snapshot_camera import _canonical_offset

    for view in VP.VIEWER_ANGLES:
        (dx, dy, dz), _up = _canonical_offset("Y", view)
        az = math.degrees(math.atan2(dx, dz)) % 360.0
        el = math.degrees(math.asin(dy / math.sqrt(dx * dx + dy * dy + dz * dz)))
        got_az, got_el = VP.VIEWER_ANGLES[view]
        assert abs(((got_az - az + 180) % 360) - 180) < 1e-4, view
        assert abs(got_el - el) < 1e-4, view


def test_twelve_adds_bottom_then_viewer_frame_fills():
    entries = VP.entries(12)
    assert _named(entries)[-1] == "bottom"
    fills = [(kind, v) for kind, v in entries if kind == "orbit"]
    assert [v for _, v in fills] == list(VP.ORBIT_FILL_AZIMUTHS)
    assert VP.angles("orbit", fills[0][1]) == (30.0, 20.0)


def test_unknown_n_has_no_protocol_plan():
    assert VP.entries(4) is None
    assert VP.entries(6) is not None


def test_protocol_constants_are_frozen():
    assert VP.SCHEME == "cc2d-views/2"
    assert VP.ANGLE_FRAME == "viewer_y_up"
    assert VP.VIEWER_ANGLES["iso"] == (45.0, 35.26439)


def test_planner_and_renderer_tables_agree():
    pytest.importorskip("sci3d_agent.mcp.tools._render.canonical_views")
    from sci3d_agent.mcp.tools._render import canonical_views as CV

    assert CV.VIEWER_ANGLES == VP.VIEWER_ANGLES
