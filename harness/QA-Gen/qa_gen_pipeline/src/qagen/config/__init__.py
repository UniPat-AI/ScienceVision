"""Profile-driven, fail-closed configuration compilation."""

from .compiler import (
    ConfigCompiler,
    ConfigCompilerError,
    build_config,
    load_compiled_config,
    revalidate_compiled_config,
)

__all__ = [
    "ConfigCompiler",
    "ConfigCompilerError",
    "build_config",
    "load_compiled_config",
    "revalidate_compiled_config",
]
