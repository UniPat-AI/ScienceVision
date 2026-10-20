"""Render-policy dispatcher. mode 1/2: strip kernel-produced images and flag."""
from __future__ import annotations
from typing import Tuple
from sci3d_agent.modes import ModeProfile

KNOWN_RENDER_PATTERNS = (
    "plt.savefig", "plt.show", ".show(", "mesh.show", "imsave", "Image.fromarray",
    "savefig(", "matplotlib", "pyvista.Plotter",
)


def _signatures(code: str) -> list:
    found = [pat for pat in KNOWN_RENDER_PATTERNS if pat in code]
    deduped = list(dict.fromkeys(found))
    return deduped or ["image_returned"]


def apply_render_policy(result: dict, profile: ModeProfile, code: str) -> Tuple[dict, list]:
    """Return (filtered_result, violations). For mode 1/2 with non-empty images,
    strip them and emit one violation flag per recognized rendering call."""
    images = result.get("images") or []
    if not images:
        return result, []
    if profile.render_policy in ("blocked", "allowed_no_pixels_returned"):
        sigs = _signatures(code)
        cleaned = dict(result)
        cleaned["images"] = []
        violations = [f"{profile.name}_render_attempt:{sig}" for sig in sigs]
        return cleaned, violations
    return result, []
