"""Regression test for apply_colormap: matplotlib.cm.get_cmap was removed in 3.9+.

See sci3d_agent/mcp/tools/_render/common.py::apply_colormap.
"""

import numpy as np
import pytest

pytest.importorskip("matplotlib")

from sci3d_agent.mcp.tools._render.common import apply_colormap


def test_apply_colormap_valid_name():
    gray = np.arange(16, dtype=np.uint8).reshape(4, 4)
    out = apply_colormap(gray, "gray")
    assert out.shape == (4, 4, 3)
    assert out.dtype == np.uint8


def test_apply_colormap_invalid_name_falls_back():
    gray = np.arange(16, dtype=np.uint8).reshape(4, 4)
    out = apply_colormap(gray, "definitely_not_a_cmap")
    assert out.shape == (4, 4, 3)
    assert out.dtype == np.uint8
