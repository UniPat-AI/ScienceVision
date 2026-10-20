"""Ground-truth filling for ScienceVision QA tasks.

A FillResult carries the computed answer plus metadata; a GroundTruthFiller
turns a raw task dict into a FillResult.  Concrete fillers live in private
``_*.py`` modules and register themselves with ``FILLERS`` at import time.
"""
from .base import FillResult, GroundTruthFiller
from .registry import FILLERS, dispatch, register

__all__ = [
    "FillResult",
    "GroundTruthFiller",
    "FILLERS",
    "dispatch",
    "register",
]

from . import _not_implemented as _not_implemented  # noqa: F401  side-effect import
