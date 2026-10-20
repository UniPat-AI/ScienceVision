"""Immutable import and taxonomy compile for frozen external selections."""

from .registry import compile_selection, import_selection
from .private_source import (
    PrivateSourceAdapterError,
    import_v32_private_sources,
    load_v32_sanitized_author_brief,
    materialize_v32_private_truth,
)

__all__ = [
    "PrivateSourceAdapterError",
    "compile_selection",
    "import_selection",
    "import_v32_private_sources",
    "load_v32_sanitized_author_brief",
    "materialize_v32_private_truth",
]
