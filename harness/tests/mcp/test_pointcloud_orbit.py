"""Pure-math tests for the point-cloud orbit rotation helper.

These tests intentionally avoid rendering (no laspy/matplotlib dependency)
so they run on GL-less / matplotlib-less machines per the renderer fallback
contract described in CLAUDE.md.
"""

import numpy as np

from sci3d_agent.mcp.tools._render.pointcloud import (
    handle_pointcloud_thumbnail,
    orbit_rotate,
)


def test_azimuth90_rotates_x_to_y():
    pts = np.array([[1.0, 0.0, 0.0]])
    out = orbit_rotate(pts, azimuth=90.0, elevation=0.0)
    assert np.allclose(out[0], [0.0, 1.0, 0.0], atol=1e-6)


def test_azimuth0_elevation0_is_identity():
    pts = np.array([[1.0, 2.0, 3.0]])
    assert np.allclose(orbit_rotate(pts, 0.0, 0.0), pts, atol=1e-6)


def test_non_numeric_azimuth_returns_error_envelope_not_raise():
    # The MCP dispatch path does not validate args against the JSON schema
    # before calling the handler, so a non-numeric azimuth/elevation must
    # return an {"error": ...} envelope rather than raise ValueError/TypeError
    # across the boundary. The path need not exist: the numeric guard must
    # fire before any file I/O or rendering is attempted.
    result = handle_pointcloud_thumbnail(
        {"path": "/nonexistent/does-not-matter.las", "azimuth": "not-a-number"}
    )
    assert "error" in result


def test_non_numeric_elevation_returns_error_envelope_not_raise():
    result = handle_pointcloud_thumbnail(
        {
            "path": "/nonexistent/does-not-matter.las",
            "azimuth": 10.0,
            "elevation": "oops",
        }
    )
    assert "error" in result
