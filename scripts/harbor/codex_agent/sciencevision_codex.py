"""Codex adapter for an OpenAI-compatible ``/v1/responses`` endpoint.

Harbor's built-in Codex agent installs the CLI and registers skills but does
not expose a way to point Codex at a custom relay. This adapter writes the
provider block into ``$CODEX_HOME/config.toml`` and leaves MCP registration to
Harbor: Harbor appends its own ``[mcp_servers.*]`` tables to the same file, and
a second copy makes Codex reject the whole file on a duplicate key.

Configuration is read from the runner environment, never from a file in the
repository:

``SV_CODEX_BASE_URL``
    Base URL of the endpoint, ending in ``/v1``.
``SV_CODEX_API_KEY``
    Credential. It is injected into the container environment and referenced
    by name from ``config.toml``; the value is never written to disk here.

Import it as ``sciencevision_codex:ScienceVisionCodex`` after putting this
directory on ``PYTHONPATH``.
"""

from __future__ import annotations

import os
import shlex

from harbor.agents.installed.codex import Codex

_BASE_URL_ENV = "SV_CODEX_BASE_URL"
_KEY_ENV = "SV_CODEX_API_KEY"
_PROVIDER_NAME = "sciencevision"


class ScienceVisionCodex(Codex):
    """Codex agent backed by an OpenAI-compatible responses endpoint."""

    def __init__(
        self,
        *args,
        model_name: str | None = None,
        extra_env: dict[str, str] | None = None,
        **kwargs,
    ) -> None:
        if not model_name:
            raise ValueError("model_name is required")

        base_url = os.environ.get(_BASE_URL_ENV, "").strip()
        if not base_url.endswith("/v1"):
            raise RuntimeError(f"{_BASE_URL_ENV} must end in /v1")

        key = os.environ.get(_KEY_ENV)
        if not key:
            raise RuntimeError(f"Required environment variable {_KEY_ENV} is absent")

        merged_env = dict(extra_env or {})
        existing = merged_env.get(_KEY_ENV)
        if existing is not None and existing != key:
            raise RuntimeError(f"Conflicting values supplied for {_KEY_ENV}")
        merged_env[_KEY_ENV] = key

        self._base_url = base_url
        super().__init__(
            *args,
            model_name=model_name,
            extra_env=merged_env,
            **kwargs,
        )

    def _provider_config(self) -> str:
        return (
            f'model_provider = "{_PROVIDER_NAME}"\n\n'
            f"[model_providers.{_PROVIDER_NAME}]\n"
            'name = "OpenAI-compatible endpoint"\n'
            f'base_url = "{self._base_url}"\n'
            f'env_key = "{_KEY_ENV}"\n'
            'wire_api = "responses"\n'
        )

    def _build_register_skills_command(self) -> str | None:
        quoted_config = shlex.quote(self._provider_config())
        commands = [
            f'test -n "${{{_KEY_ENV}:-}}" || '
            f'{{ echo "{_KEY_ENV} is absent" >&2; exit 1; }}',
            "umask 077",
            f'printf %s {quoted_config} > "$CODEX_HOME/config.toml"',
            'chmod 600 "$CODEX_HOME/config.toml"',
        ]
        parent_command = super()._build_register_skills_command()
        if parent_command:
            commands.append(parent_command)
        return "\n".join(commands)
