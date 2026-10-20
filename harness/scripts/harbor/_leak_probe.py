"""Detect out-of-sandbox information acquisition in an agent trajectory.

This is the *producer* for ``leak_scan.json``. The 2026-08-14 EV2 report
consumed that file (``build_report.py`` reads ``fetch_attempt`` /
``local_asset_file`` / ``fetch_url``) but no producer was ever committed, so the
one artifact that says whether a six-baseline run was contaminated could not be
reproduced. This module closes that gap.

Why it matters concretely: ``[agent]`` in a generated task.toml carries only
``timeout_sec`` — ``network_mode`` is applied to ``[verifier]`` alone
(``build_harbor_tasks._render_task_toml``). The agent therefore has public
egress, and the cc2d family (which is denied the raw 3D file by design) can
fetch the original asset from its public source. Biology is the domain where
cc2d's score approaches cc's, which is exactly the shape a leak produces.

Three escalating signals per trial, deliberately kept independent:

    curl              a content fetcher was invoked       (capability + intent)
    fetch_attempt     an external host was named          (target identified)
    local_asset_file  an asset file exists outside /data  (it worked)

Only the third is proof. Reporting all three lets a reviewer distinguish
"tried and failed" from "never tried" from "succeeded".

Two scanning asymmetries, both intentional:

* URLs are read from **agent-authored text only** (tool-call arguments), never
  from observations. A URL in a pip traceback or a search result is a
  consequence, not an intent, and counting it manufactures false positives.
* Asset filenames are read from observations too, but there only when the token
  is path-like. ``ls /data/`` prints bare staged filenames; treating those as
  leaks would flag every honest cc/ccmcp trial.

Staged inputs are never leaks: a token under ``/data/`` is what the stager put
there (``COPY asset/ /data/`` for codex/codex-mcp, ``COPY views/ /data/`` for
cc2d).

Trajectory schema pinned against ``harbor_adapters/sci3d/_translate.py:66-84``:
``step["tool_calls"][i] = {tool_call_id, function_name, arguments{}}`` and
``step["observation"]["results"][i]["content"]``.

Pure detection only — no filesystem walking beyond one trial, no CLI. The
run-walking driver is ``scripts/harbor/leak_scan.py``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from harbor_adapters.sci3d._integrity_policy import audit_integrity_policy

LEAK_SCAN_SCHEMA_VERSION = "sciencevision.leak_scan.v1"

# Evidence is for a human reader, not a corpus dump: a 200-step run must not be
# able to inflate the report by three orders of magnitude.
_MAX_EVIDENCE = 10

# 3D asset extensions this repo actually stages. Compound suffixes are listed
# because ``.nii.gz`` is the medical default and str.endswith takes a tuple.
ASSET_EXTS: tuple[str, ...] = (
    # volumetric / medical
    ".nii.gz", ".nii", ".mha", ".mhd", ".nrrd", ".dcm",
    # molecular
    ".cif", ".mmcif", ".pdb", ".pdb.gz", ".ent", ".xyz", ".sdf", ".mol2", ".mol",
    # mesh / point cloud
    ".obj", ".ply", ".stl", ".off", ".glb", ".gltf", ".fbx", ".dae",
    # CAD
    ".step", ".stp", ".iges", ".igs", ".3mf", ".sldprt", ".sldasm",
    # simulation grids
    ".vtk", ".vtu", ".vti",
)

# Content fetchers only. Package managers are tracked separately (see _PKG_RE)
# so this cell stays comparable with the 2026-08-14 numbers: `pip install` is
# real egress but cannot fetch a PDB entry, and folding it in here would
# silently redefine the metric.
_FETCHER_RE = re.compile(
    r"(?:"
    r"\bcurl\b|\bwget\b|\baria2c?\b|\baxel\b|\bhttpie\b"
    r"|\bgit\s+(?:clone|fetch|pull)\b"
    r"|\bscp\b|\bsftp\b|\brsync\b|\blftp\b"
    r"|urllib\.request|urlretrieve|urlopen"
    r"|requests\.(?:get|post)|httpx\.(?:get|post)|aiohttp"
    r"|hf_hub_download|snapshot_download|huggingface_hub"
    r"|\bnetcat\b|openssl\s+s_client"
    r")",
    re.IGNORECASE,
)

_PKG_RE = re.compile(
    r"(?:\bpip3?\s+(?:install|download)\b|\bnpm\s+(?:install|i)\b"
    r"|\byarn\s+add\b|\bapt(?:-get)?\s+install\b|\bconda\s+install\b)",
    re.IGNORECASE,
)

# Harness tools that reach the network without naming a binary. All of these sit
# in _GENERIC_HARNESS_TOOLS, so the process-reward tool policy never flags them.
_FETCHER_TOOLS = frozenset({"webfetch", "websearch", "web_fetch", "web_search", "fetch"})

_URL_RE = re.compile(r"(?:https?|ftps?)://([A-Za-z0-9._\-\[\]:]+)")

# ccmcp legitimately talks to SAM_SERVICE_URL on loopback; that is in-sandbox.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"})

_STAGED_PREFIXES = ("/data/", "data/")

_TOKEN_SPLIT_RE = re.compile(r"[\s'\"(),;=|&<>\[\]{}`]+")

_PORT_SUFFIX_RE = re.compile(r":\d+$")

_TRAJECTORY_NAMES = ("sci3d_trajectory.json", "trajectory.json")


# -- text extraction ------------------------------------------------------- #
def _tool_calls(step: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    calls = step.get("tool_calls")
    if not isinstance(calls, list):
        return []
    return [c for c in calls if isinstance(c, Mapping)]


def _arguments_text(call: Mapping[str, Any]) -> str:
    """Flatten a tool call's arguments to searchable text.

    ``_translate`` guarantees a dict, but a hand-written or third-party
    trajectory may carry a bare string; both must scan rather than crash.
    """
    args = call.get("arguments")
    if isinstance(args, Mapping):
        return " ".join(str(v) for v in args.values())
    return "" if args is None else str(args)


def _observation_text(step: Mapping[str, Any]) -> str:
    obs = step.get("observation")
    if not isinstance(obs, Mapping):
        return ""
    results = obs.get("results")
    if not isinstance(results, list):
        return ""
    return "\n".join(
        str(r.get("content") or "") for r in results if isinstance(r, Mapping)
    )


# -- signal detection ------------------------------------------------------ #
def _is_fetcher_tool(name: str) -> bool:
    low = name.lower().replace("-", "_")
    if low in _FETCHER_TOOLS:
        return True
    return low.endswith("__read_url") or low.endswith("__search_web")


def _is_staged(token: str) -> bool:
    """True when the token points inside the stager's own output directory."""
    norm = token[2:] if token.startswith("./") else token
    return norm.startswith(_STAGED_PREFIXES)


def _asset_tokens(text: str, *, require_path: bool) -> tuple[str, ...]:
    """Asset-extension tokens in ``text``, excluding staged paths.

    ``require_path=True`` (observations) keeps only path-like tokens, so the
    bare filenames printed by ``ls /data/`` do not read as downloads.
    """
    found: list[str] = []
    for raw in _TOKEN_SPLIT_RE.split(text):
        token = raw.strip().strip(":,")
        if not token or not token.lower().endswith(ASSET_EXTS):
            continue
        if _is_staged(token):
            continue
        if require_path and "/" not in token:
            continue
        found.append(token)
    return tuple(found)


def _external_hosts(text: str) -> tuple[str, ...]:
    hosts: list[str] = []
    for match in _URL_RE.finditer(text):
        host = _PORT_SUFFIX_RE.sub("", match.group(1))
        low = host.lower()
        if low in _LOCAL_HOSTS or low.endswith(".local"):
            continue
        hosts.append(host)
    return tuple(hosts)


def _dedup(values: list[str]) -> list[str]:
    """Order-preserving dedup, so evidence reads in the order it happened."""
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


# -- public API ------------------------------------------------------------ #
def scan_trajectory(traj: Mapping[str, Any] | None) -> dict[str, Any]:
    """Scan one parsed ATIF trajectory. Returns a new dict; never mutates."""
    raw_steps = traj.get("steps") if isinstance(traj, Mapping) else None
    steps = (
        [s for s in raw_steps if isinstance(s, Mapping)]
        if isinstance(raw_steps, list)
        else []
    )

    fetchers: list[str] = []
    hosts: list[str] = []
    asset_files: list[str] = []
    package_manager = False
    integrity_calls: list[dict[str, Any]] = []

    for step in steps:
        for call in _tool_calls(step):
            name = str(call.get("function_name") or "")
            arg_text = _arguments_text(call)
            integrity_calls.append({
                "name": name,
                "arguments_text": arg_text,
                "step": step.get("step_id"),
            })
            if _is_fetcher_tool(name):
                fetchers.append(name)
            match = _FETCHER_RE.search(arg_text)
            if match:
                fetchers.append(match.group(0).strip())
            if _PKG_RE.search(arg_text):
                package_manager = True
            hosts.extend(_external_hosts(arg_text))
            asset_files.extend(_asset_tokens(arg_text, require_path=False))
        asset_files.extend(_asset_tokens(_observation_text(step), require_path=True))

    integrity = audit_integrity_policy(integrity_calls)

    return {
        "scanned": True,
        "curl": bool(fetchers),
        "fetch_attempt": bool(hosts),
        "fetch_url": hosts[0] if hosts else None,
        "local_asset_file": bool(asset_files),
        "protected_path_attempt": bool(integrity["protected_path_attempts"]),
        "unauthorized_egress_attempt": bool(integrity["unauthorized_egress_attempts"]),
        "steps": len(steps),
        "evidence": {
            "fetcher": _dedup(fetchers)[:_MAX_EVIDENCE],
            "hosts": _dedup(hosts)[:_MAX_EVIDENCE],
            "asset_files": _dedup(asset_files)[:_MAX_EVIDENCE],
            "package_manager": package_manager,
            "integrity": integrity,
        },
    }


def unscannable(reason: str) -> dict[str, Any]:
    """A row we could not read. Tri-state on purpose.

    ``None`` rather than ``False`` because "checked and clean" and "could not
    check" must never render identically — that conflation is how a gate reports
    a clean bill of health it never earned.
    """
    return {
        "scanned": False,
        "curl": None,
        "fetch_attempt": None,
        "fetch_url": None,
        "local_asset_file": None,
        "protected_path_attempt": None,
        "unauthorized_egress_attempt": None,
        "steps": 0,
        "evidence": {
            "fetcher": [],
            "hosts": [],
            "asset_files": [],
            "package_manager": None,
            "integrity": None,
        },
        "reason": reason,
    }


def scan_trial(trial_dir: Path) -> dict[str, Any]:
    """Scan a Harbor trial directory (``<trial>/agent/*trajectory.json``).

    Trajectory preference matches ``collect_qagen_results.collect_one``:
    Sci3dAgent's ``sci3d_trajectory.json`` wins over the CLI's
    ``trajectory.json`` when both are archived.
    """
    agent_dir = Path(trial_dir) / "agent"
    for name in _TRAJECTORY_NAMES:
        path = agent_dir / name
        if not path.is_file():
            continue
        try:
            traj = json.loads(path.read_text())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return unscannable(f"{name}: {type(exc).__name__}")
        return {**scan_trajectory(traj), "trajectory_file": name}
    return unscannable("no agent trajectory found")


def summarize(
    per_task: Mapping[str, Mapping[str, Mapping[str, Any]]],
    domains: Mapping[str, str],
) -> dict[str, dict[str, dict[str, int]]]:
    """Roll per-task rows up to ``summary[route][domain]`` counts.

    Cell keys are pinned to the existing consumer: ``curl_or_wget`` /
    ``external_fetch`` / ``local_asset_file`` / ``n``. ``unscanned`` is additive
    and safe for an older reader to ignore. Note the deliberate ``curl``
    (per-task) vs ``curl_or_wget`` (summary) name difference — it exists in the
    shipped artifact, and renaming either half would break ``build_report.py``.
    """
    out: dict[str, dict[str, dict[str, int]]] = {}
    for route, tasks in (per_task or {}).items():
        cells: dict[str, dict[str, int]] = {}
        for task_id, row in (tasks or {}).items():
            domain = str((domains or {}).get(task_id) or "unknown")
            cell = cells.setdefault(
                domain,
                {
                    "curl_or_wget": 0,
                    "external_fetch": 0,
                    "local_asset_file": 0,
                    "protected_path_attempt": 0,
                    "unauthorized_egress_attempt": 0,
                    "unscanned": 0,
                    "n": 0,
                },
            )
            cell["n"] += 1
            if not row.get("scanned", True):
                cell["unscanned"] += 1
                continue
            if row.get("curl"):
                cell["curl_or_wget"] += 1
            if row.get("fetch_attempt"):
                cell["external_fetch"] += 1
            if row.get("local_asset_file"):
                cell["local_asset_file"] += 1
            if row.get("protected_path_attempt"):
                cell["protected_path_attempt"] += 1
            if row.get("unauthorized_egress_attempt"):
                cell["unauthorized_egress_attempt"] += 1
        out[route] = cells
    return out
