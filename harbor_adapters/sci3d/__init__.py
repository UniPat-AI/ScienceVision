"""Harbor adapter for the sci3d_agent loop.

Exposes ``Sci3dAgent`` (a ``harbor.agents.base.BaseAgent``) and
``Sci3dVerifier`` (a ``harbor.verifier.base.BaseVerifier``), so the sci3d
agent + verify can be driven by::

    harbor run \
        --agent-import-path harbor_adapters.sci3d.agent:Sci3dAgent \
        --verifier-import-path harbor_adapters.sci3d.verifier:Sci3dVerifier

``Sci3dAgent`` / ``Sci3dVerifier`` subclass Harbor base classes, so importing
them requires ``harbor`` to be installed. The containerized scorer image is
deliberately Harbor-free (it only runs ``_score_core.score_rewards`` via
``tests/score.py``), so these top-level imports are made tolerant: if ``harbor``
is absent the names are simply unavailable, while ``harbor_adapters.sci3d
._score_core`` (and the scorer functions it calls) stay importable.
"""
try:  # harbor present (host-run route / full env): export the adapters.
    from .agent import Sci3dAgent
    from .verifier import Sci3dVerifier

    __all__ = ["Sci3dAgent", "Sci3dVerifier"]
except ModuleNotFoundError:  # harbor-free scorer image: scoring core only.
    __all__ = []
