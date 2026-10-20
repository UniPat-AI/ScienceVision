"""Runtime receipt coverage without requiring a live Docker daemon."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from harbor_adapters.sci3d._route_contracts import (  # noqa: E402
    MCP_HTTP_HEALTH_PATH,
    MCP_SERVICE_NAME,
    build_route_contract,
    canonical_catalog,
    validate_health_receipt,
)
from scripts.harbor._mcp_route_runtime import (  # noqa: E402
    write_route_health_receipt_from_environment,
)


def _surface() -> dict:
    surface = {
        "schema_version": "sciencevision.mcp_surface.v1",
        "server": "sciencevision",
        "viewer_family": "gltf.v1",
        "tools": [
            {"name": "sv3d_open_scene", "schema_sha256": "open-schema"},
            {"name": "sv3d_render", "schema_sha256": "render-schema"},
        ],
    }
    canonical = canonical_catalog(surface)
    assert canonical is not None
    surface.update(
        {
            key: canonical[key]
            for key in ("tool_names", "tool_count", "catalog_sha256", "schema_sha256")
        }
    )
    return surface


class _WorkerEnvironment:
    def __init__(self, responses: dict[str, object]):
        self.responses = responses
        self.calls: list[tuple[str, str | None, int | None]] = []

    async def service_exec(self, command, *, service=None, timeout_sec=None, **_kwargs):
        self.calls.append((command, service, timeout_sec))
        response = self.responses[command]
        if isinstance(response, Exception):
            raise response
        return response


def test_cc_writes_negative_guarantee_without_touching_a_worker(tmp_path: Path):
    contract = build_route_contract("cc")
    environment = _WorkerEnvironment({})

    receipt = asyncio.run(
        write_route_health_receipt_from_environment(
            tmp_path,
            contract,
            environment,
            registered_servers=[],
        )
    )

    assert environment.calls == []
    assert (tmp_path / "mcp_health.json").is_file()
    assert receipt["runtime"]["probe"] == "cc_negative_guarantee"
    assert validate_health_receipt(receipt, contract)["status"] == "eligible"


def test_ccmcp_reads_private_health_and_exact_live_surface(tmp_path: Path):
    surface = _surface()
    contract = build_route_contract(
        "ccmcp",
        viewer_family="gltf.v1",
        allowed_tools=("sv3d_open_scene",),
        required_tool_groups=(("sv3d_open_scene",),),
        min_successful_calls=1,
        catalog=surface,
    )
    worker_health = {
        "schema_version": "sciencevision.mcp_worker_health.v1",
        "status": "ready",
        "http_surface": "http://127.0.0.1:8765/mcp",
        "viewer_family": "gltf.v1",
        "catalog_sha256": surface["catalog_sha256"],
        "schema_sha256": surface["schema_sha256"],
    }
    environment = _WorkerEnvironment(
        {
            f"cat {MCP_HTTP_HEALTH_PATH}": SimpleNamespace(
                return_code=0, stdout=json.dumps(worker_health), stderr=""
            ),
            "sciencevision-mcp --surface": SimpleNamespace(
                return_code=0, stdout=json.dumps(surface), stderr=""
            ),
        }
    )

    receipt = asyncio.run(
        write_route_health_receipt_from_environment(
            tmp_path,
            contract,
            environment,
            registered_servers=[SimpleNamespace(name="sciencevision")],
        )
    )

    assert environment.calls == [
        (f"cat {MCP_HTTP_HEALTH_PATH}", MCP_SERVICE_NAME, 20),
        ("sciencevision-mcp --surface", MCP_SERVICE_NAME, 20),
    ]
    assert receipt["worker_health"] == worker_health
    assert receipt["server_surface"] == surface
    assert receipt["runtime"]["receipt_source"] == "harbor_route_codex_adapter"
    assert validate_health_receipt(receipt, contract)["status"] == "eligible"


def test_ccmcp_probe_failure_still_writes_an_infra_invalid_receipt(tmp_path: Path):
    contract = build_route_contract("ccmcp", catalog=_surface())
    environment = _WorkerEnvironment(
        {
            f"cat {MCP_HTTP_HEALTH_PATH}": RuntimeError("worker unavailable"),
            "sciencevision-mcp --surface": SimpleNamespace(
                return_code=2, stdout="bad surface", stderr="",
            ),
        }
    )

    receipt = asyncio.run(
        write_route_health_receipt_from_environment(
            tmp_path,
            contract,
            environment,
            registered_servers=["sciencevision"],
        )
    )

    assert set(receipt["runtime"]["probe_errors"]) == {"worker_health", "server_surface"}
    verdict = validate_health_receipt(receipt, contract)
    assert verdict["status"] == "infra_invalid"
    assert "missing_server_surface" in verdict["reason_codes"]
