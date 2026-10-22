"""Codex adapter for the ScienceVision UniAPI relay.

What this owns
--------------

One thing: the model provider. Harbor's built-in Codex agent installs the CLI
and registers skills, but it has no way to point Codex at the UniAPI relay, so
this adapter writes the provider block into ``$CODEX_HOME/config.toml`` (see
``config.toml`` beside this module, which carries the base URL and names the
environment variable the key is read from -- never the key itself). The model is
restricted to ``_ALLOWED_MODELS`` (gpt-5.6-sol / gpt-6-astra).

What this does NOT own
----------------------

MCP registration. Harbor already implements it for Codex:
``harbor/agents/installed/codex.py::_build_register_mcp_servers_command``
renders ``[mcp_servers.<name>]`` tables and **appends** them to the same
``$CODEX_HOME/config.toml``, from ``self.mcp_servers`` -- the value
``--mcp-config`` populates. Adding a second copy here would be a duplicate TOML
key, and Codex rejects the *entire* file on a duplicate -- the provider block
included. Measured 2026-09-22: a run whose adapter appended its own
``[mcp_servers.sciencevision]`` produced 16/16 trials at reward 0.0 with zero
MCP calls, each logging ``Error loading config.toml: ... duplicate key``.

So: if a ccmcp run reports zero MCP calls, the bug is not here. Check
``agent/codex.txt`` for ``duplicate key``, then count MCP calls from Codex's
session rollout rather than from the ATIF trajectory -- the ATIF conversion
loses them (see ``harbor_adapters/sci3d/_tool_outcomes.py::parse_codex_mcp_calls``).

Deployment
----------

The harness imports this module as ``sciencevision_codex:ScienceVisionUniAPICodex``,
so this directory must be on ``PYTHONPATH`` alongside the build context. The
launcher stages ``config.toml`` next to the module.
"""

from __future__ import annotations

import os
import shlex
import tomllib
from pathlib import Path

from harbor.agents.installed.codex import Codex


_ALLOWED_MODELS = ("gpt-5.6-sol", "gpt-6-astra", "gpt-6.1-sol")


class ScienceVisionUniAPICodex(Codex):
    """Codex adapter that installs the ScienceVision UniAPI provider config."""

    _KEY_NAME = "UNIAPI_KEY_SCIENCEVISION"
    _PROVIDER_NAME = "uniapi-sciencevision"

    def __init__(
        self,
        *args,
        model_name: str | None = None,
        extra_env: dict[str, str] | None = None,
        **kwargs,
    ) -> None:
        if model_name not in _ALLOWED_MODELS:
            raise ValueError(
                f"This adapter is pinned to a model_name in {_ALLOWED_MODELS!r}, got {model_name!r}"
            )

        key = os.environ.get(self._KEY_NAME)
        if not key:
            raise RuntimeError(f"Required environment variable {self._KEY_NAME} is absent")

        merged_env = dict(extra_env or {})
        existing = merged_env.get(self._KEY_NAME)
        if existing is not None and existing != key:
            raise RuntimeError(f"Conflicting values supplied for {self._KEY_NAME}")
        merged_env[self._KEY_NAME] = key

        super().__init__(
            *args,
            model_name=model_name,
            extra_env=merged_env,
            **kwargs,
        )

    def _build_register_skills_command(self) -> str | None:
        config_path = Path(__file__).with_name("config.toml")
        config_text = config_path.read_text(encoding="utf-8")
        parsed = tomllib.loads(config_text)

        provider = parsed.get("model_providers", {}).get(self._PROVIDER_NAME, {})
        if parsed.get("model_provider") != self._PROVIDER_NAME:
            raise RuntimeError("Provider config does not select uniapi-sciencevision")
        if provider.get("env_key") != self._KEY_NAME:
            raise RuntimeError("Provider config does not use UNIAPI_KEY_SCIENCEVISION")
        if provider.get("wire_api") != "responses":
            raise RuntimeError("Provider config must use the responses wire API")
        if not str(provider.get("base_url", "")).endswith("/v1"):
            raise RuntimeError("Provider base_url must end in /v1")

        quoted_config = shlex.quote(config_text)
        commands = [
            f'test -n "${{{self._KEY_NAME}:-}}" || '
            f'{{ echo "{self._KEY_NAME} is absent" >&2; exit 1; }}',
            "umask 077",
            f'printf %s {quoted_config} > "$CODEX_HOME/config.toml"',
            'chmod 600 "$CODEX_HOME/config.toml"',
        ]

        parent_command = super()._build_register_skills_command()
        if parent_command:
            commands.append(parent_command)
        return "\n".join(commands)
