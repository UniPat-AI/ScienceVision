"""Model routing and credential resolution for the ScienceVision LLM layer.

All functions are pure (no I/O at import time).  ``load_credentials`` reads
env vars and, as a last resort, the repo-root ``.env`` file — but only when
actually called.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .client import ChatClientError


def _resolve_repo_root_env() -> Path:
    """Return the path to the ``.env`` file at the git repository root.

    Works from both the main checkout and any git worktree.  Uses
    ``git rev-parse --git-common-dir`` to find the shared ``.git`` directory,
    then derives the main-checkout root from it.  Falls back to four parents
    above this file if git is unavailable.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent,
            timeout=5,
        )
        if result.returncode == 0:
            git_common = Path(result.stdout.strip())
            if not git_common.is_absolute():
                git_common = (Path(__file__).parent / git_common).resolve()
            # git-common-dir is either <repo>/.git  or  <repo>/.git itself
            # (for worktrees it's the original repo's .git directory)
            repo_root = git_common.parent
            return repo_root / ".env"
    except (OSError, subprocess.TimeoutExpired):
        pass
    # Fallback: assume main checkout layout (no worktree nesting)
    return Path(__file__).resolve().parents[4] / ".env"


# Path to the main checkout .env (worktrees share one repo root .env).
_REPO_ROOT_ENV = _resolve_repo_root_env()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_env_file(path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE .env file; skip blank lines and comments."""
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw_value = line.partition("=")
        value = raw_value.strip()
        # Strip trailing inline comment only from unquoted values (e.g. KEY=val  # note)
        if not (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in ("'", '"')
        ):
            if " #" in value:
                value = value.split(" #", 1)[0].strip()
        # Strip surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        result[key.strip()] = value
    return result


def _normalize_base_url(url: str) -> str:
    """Ensure the URL ends with ``/v1``."""
    url = url.rstrip("/")
    if not url.endswith("/v1"):
        url = url + "/v1"
    return url


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_model(v_class: str, config: dict) -> str:
    """Return the model id for *v_class* according to *config*.

    Priority order (highest first):
    1. ``config['llm']['force_model']`` — overrides everything.
    2. ``config['llm']['model_by_v_class'][v_class]``
    3. ``config['llm']['default_model']``

    Raises:
        ValueError: If no model can be resolved (config missing or empty).
    """
    llm_cfg: dict = config.get("llm", {})

    force = llm_cfg.get("force_model")
    if force is not None:
        if not isinstance(force, str) or not force.strip():
            raise ValueError("llm.force_model must be a non-empty string when present")
        return force

    by_class = llm_cfg.get("model_by_v_class", {})
    if v_class in by_class:
        return by_class[v_class]

    default = llm_cfg.get("default_model")
    if default:
        return default

    raise ValueError(
        f"resolve_model: no model resolved for v_class={v_class!r}. "
        "Provide config['llm']['force_model'], "
        "config['llm']['model_by_v_class'][v_class], or "
        "config['llm']['default_model']."
    )


def cross_model_for(builder_model: str, config: dict) -> str:
    """Return a model DIFFERENT from *builder_model* for challenger review.

    Lookup order:
    1. ``config['llm']['challenger_cross_model_map'][builder_model]``
    2. First entry in ``config['llm']['model_pool']`` that is not
       *builder_model*.
    3. *builder_model* itself (degenerate case — pool has only one entry).
    """
    llm_cfg: dict = config.get("llm", {})

    cross_map: dict = llm_cfg.get("challenger_cross_model_map", {})
    if builder_model in cross_map:
        return cross_map[builder_model]

    pool: list[str] = llm_cfg.get("model_pool", [])
    for model in pool:
        if model != builder_model:
            return model

    # Degenerate: pool is empty or only contains builder_model
    return builder_model


def load_credentials() -> tuple[str, str]:
    """Resolve ``(base_url, api_key)`` for the Mindra OpenAI-compat gateway.

    Priority order:
    1. ``MINDRACODE_API_BASE`` + ``MINDRACODE_API_KEY`` env vars.
    2. ``MINDRA_BASE_URL`` + ``MINDRA_API`` env vars.
    3. ``.env`` at the git repository root (resolved via ``_REPO_ROOT_ENV``)
       containing ``MINDRA_BASE_URL`` and ``MINDRA_API`` lines.
    4. ``ANTHROPIC_BASE_URL`` + ``ANTHROPIC_API_KEY`` env vars.

    The returned *base_url* is normalised to end with ``/v1``.

    Raises:
        ChatClientError: If credentials cannot be resolved through any path.
    """
    # 1. MINDRACODE_ env vars
    base = os.environ.get("MINDRACODE_API_BASE", "").strip()
    key = os.environ.get("MINDRACODE_API_KEY", "").strip()
    if base and key:
        return _normalize_base_url(base), key

    # 2. MINDRA_ env vars
    base = os.environ.get("MINDRA_BASE_URL", "").strip()
    key = os.environ.get("MINDRA_API", "").strip()
    if base and key:
        return _normalize_base_url(base), key

    # 3. .env file at repo root
    env_vars = _load_env_file(_REPO_ROOT_ENV)
    base = env_vars.get("MINDRA_BASE_URL", "").strip()
    key = env_vars.get("MINDRA_API", "").strip()
    if base and key:
        return _normalize_base_url(base), key

    # 4. ANTHROPIC_ env vars (Mindra is an OpenAI-compat Anthropic proxy in
    # the typical dev setup on this machine)
    base = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if base and key:
        return _normalize_base_url(base), key

    raise ChatClientError(
        "load_credentials: could not resolve Mindra gateway credentials. "
        "Set MINDRACODE_API_BASE + MINDRACODE_API_KEY (or MINDRA_BASE_URL + "
        "MINDRA_API) env vars, or provide a .env at "
        f"{_REPO_ROOT_ENV}."
    )
