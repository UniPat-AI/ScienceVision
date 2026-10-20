"""Browser-MCP trajectory capture: capability evidence, store, and HTTP route.

The Functions panel and the Trajectory drawer are only usable when three things
line up: the runtime advertises ``viewer-mcp-functions``, the expert page carries
the submit endpoint and CSRF metas, and ``/api/expert/trajectory`` accepts an
authenticated append-only submission. Each is covered below.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterator

import pytest

from online_harness import request_json, serve
from tools.offline_task_manager.bundle import (
    build_runtime_manifest,
    require_runtime_compatibility,
)
from tools.offline_task_manager.contract import (
    ContractError,
    TRAJECTORY_SCHEMA_VERSION,
    canonical_json,
)
from tools.offline_task_manager.online_server import EXPERT_APP_ROOT
from tools.offline_task_manager.trajectories import ensure_schema, import_trajectory

BUNDLE_ID = "bndl_fixture"
BUNDLE_TASK_IDS = ("task-1", "task-2")
MCP_CONTROL_PATHS = (
    "expert-app/mcp/task-mcp-controls.js",
    "expert-app/mcp/task-mcp-controls.css",
    "expert-app/mcp/task-trajectory.js",
    "expert-app/mcp/task-trajectory.css",
)


def _payload(**overrides: Any) -> dict[str, Any]:
    """One exported trajectory document, shaped like the drawer's Submit body."""

    document = {
        "schema_version": TRAJECTORY_SCHEMA_VERSION,
        "task_id": "task-1",
        "qualified_task_id": f"{BUNDLE_ID}:task-1",
        "context_fingerprint": "ctx-e3b0c442",
        "session_id": "sess-0001",
        "submission_id": "trj_0001",
        "idempotency_key": "idem-0001",
        "status": "submitted",
        "created_at": "2026-08-19T00:00:00Z",
        "updated_at": "2026-08-19T00:01:30Z",
        "task_input": {"answer": "Coplanar within tolerance."},
        "trajectory": [
            {
                "step_id": "step-1",
                "step": 1,
                "function": "viewer_reset",
                "input": {},
                "result": {"ok": True, "policy_receipt": "viewer_utility"},
                "screenshot": {
                    "data_url": "data:image/png;base64,iVBORw0KGgo=",
                    "captured_at": "2026-08-19T00:00:30Z",
                },
            }
        ],
        "final_result": {"answer": "Coplanar within tolerance."},
    }
    return document | overrides


def _identity(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "storage_key": f"{payload['qualified_task_id']}::{payload['context_fingerprint']}",
        "qualified_task_id": payload["qualified_task_id"],
        "context_fingerprint": payload["context_fingerprint"],
        "submission_id": payload["submission_id"],
        "idempotency_key": payload["idempotency_key"],
    }


class Trajectories:
    """A schema-ready connection plus the artifact root that pairs with it."""

    def __init__(self, connection: sqlite3.Connection, root: Path) -> None:
        self.connection = connection
        self.root = root

    def submit(
        self,
        payload: dict[str, Any],
        *,
        expert_id: str = "expert-a",
        identity: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return import_trajectory(
            self.connection,
            expert_id=expert_id,
            bundle_id=BUNDLE_ID,
            bundle_task_ids=BUNDLE_TASK_IDS,
            payload=payload,
            identity=identity if identity is not None else _identity(payload),
            artifact_root=self.root,
            received_at="2026-08-19T00:02:00Z",
        )

    def artifact(self, submission_id: str) -> Path:
        return self.root / BUNDLE_ID / f"{submission_id}.json"

    def rows(self) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                "SELECT * FROM trajectory_submissions ORDER BY idempotency_key"
            )
        )


@pytest.fixture
def trajectories(tmp_path: Path) -> Iterator[Trajectories]:
    connection = sqlite3.connect(tmp_path / "manager.sqlite3")
    connection.row_factory = sqlite3.Row
    ensure_schema(connection)
    try:
        yield Trajectories(connection, tmp_path / "trajectories")
    finally:
        connection.close()


def test_runtime_manifest_needs_both_halves_of_the_mcp_layer(workflow: dict) -> None:
    viewer_root: Path = workflow["viewer"]
    before = build_runtime_manifest(viewer_root, expert_app_root=EXPERT_APP_ROOT)
    # The parent-page half ships with the host, so the in-iframe half decides.
    assert "viewer-mcp-functions" not in before["capabilities"]

    (viewer_root / "shared" / "viewer-mcp-api.js").write_text(
        "// in-iframe MCP command boundary\n", encoding="utf-8"
    )
    after = build_runtime_manifest(viewer_root, expert_app_root=EXPERT_APP_ROOT)
    assert "viewer-mcp-functions" in after["capabilities"]

    evidence = after["capability_evidence"]["viewer-mcp-functions"]
    assert [record["path"] for record in evidence] == [
        "shared/viewer-mcp-api.js",
        *MCP_CONTROL_PATHS,
    ]
    assert all(len(record["sha256"]) == 64 for record in evidence)
    assert all(record["byte_size"] > 0 for record in evidence)
    # New files change the runtime identity, which is what pins the two halves
    # together for a given deployment.
    assert after["runtime_id"] != before["runtime_id"]


def test_added_capability_stays_backward_compatible(workflow: dict) -> None:
    viewer_root: Path = workflow["viewer"]
    (viewer_root / "shared" / "viewer-mcp-api.js").write_text("//\n", encoding="utf-8")
    runtime = build_runtime_manifest(viewer_root, expert_app_root=EXPERT_APP_ROOT)
    requirement = {
        "loader_api": runtime["loader_api"],
        "renderers": ["molstar"],
        "capabilities": ["expert-review", "task-screenshot"],
    }
    # A bundle built before browser MCP existed must still run on this runtime.
    require_runtime_compatibility(requirement, runtime)
    with pytest.raises(ContractError, match="required capabilities"):
        require_runtime_compatibility(
            requirement | {"capabilities": ["viewer-mcp-evidence"]}, runtime
        )


def test_import_writes_the_canonical_document_and_its_metadata(
    trajectories: Trajectories,
) -> None:
    payload = _payload()
    acknowledgment = trajectories.submit(payload)
    assert acknowledgment["acknowledged"] is True
    assert acknowledgment["replayed"] is False
    assert acknowledgment["submission_id"] == "trj_0001"
    assert acknowledgment["step_count"] == 1

    artifact = trajectories.artifact("trj_0001")
    body = canonical_json(payload).encode("utf-8")
    assert artifact.read_bytes() == body
    assert acknowledgment["byte_size"] == len(body)
    assert acknowledgment["payload_sha256"] == hashlib.sha256(body).hexdigest()

    (row,) = trajectories.rows()
    assert row["artifact_path"] == f"{BUNDLE_ID}/trj_0001.json"
    assert row["qualified_task_id"] == f"{BUNDLE_ID}:task-1"
    assert row["received_at"] == "2026-08-19T00:02:00Z"


def test_import_replays_an_identical_body_and_refuses_a_changed_one(
    trajectories: Trajectories,
) -> None:
    payload = _payload()
    first = trajectories.submit(payload)
    replayed = trajectories.submit(_payload())
    assert replayed == first | {"replayed": True}
    assert len(trajectories.rows()) == 1

    changed = _payload(final_result={"answer": "Not coplanar."})
    with pytest.raises(ContractError, match="reused for a different payload"):
        trajectories.submit(changed)
    # The stored evidence is what the expert actually submitted first.
    assert trajectories.artifact("trj_0001").read_bytes() == canonical_json(
        payload
    ).encode("utf-8")


def test_import_refuses_cross_expert_keys_and_reused_submission_ids(
    trajectories: Trajectories,
) -> None:
    payload = _payload()
    trajectories.submit(payload)

    with pytest.raises(ContractError, match="belongs to another expert"):
        trajectories.submit(_payload(), expert_id="expert-b")

    reused = _payload(idempotency_key="idem-0002")
    with pytest.raises(ContractError, match="already recorded under a different"):
        trajectories.submit(reused)
    # The refusal happens before the write, so the first document survives.
    assert trajectories.artifact("trj_0001").read_bytes() == canonical_json(
        payload
    ).encode("utf-8")
    assert len(trajectories.rows()) == 1


def test_import_validates_scope_shape_and_agreement(
    trajectories: Trajectories,
) -> None:
    with pytest.raises(ContractError, match="not in the task bundle for this expert"):
        trajectories.submit(_payload(task_id="task-9"))
    with pytest.raises(ContractError, match="schema_version must be"):
        trajectories.submit(_payload(schema_version="sciencevision.other.v1"))
    with pytest.raises(ContractError, match="must be a list of steps"):
        trajectories.submit(_payload(trajectory={"step": 1}))
    with pytest.raises(ContractError, match="every trajectory step must be an object"):
        trajectories.submit(_payload(trajectory=["viewer_reset"]))

    payload = _payload()
    with pytest.raises(ContractError, match="disagrees between identity and payload"):
        trajectories.submit(
            payload, identity=_identity(payload) | {"submission_id": "trj_9999"}
        )
    # Nothing above may leave an artifact behind.
    assert not trajectories.root.exists() or not list(trajectories.root.rglob("*.json"))
    assert trajectories.rows() == []


def _publish(gateway: Any, store: Any) -> None:
    """Give every expert an active task bundle, the way an admin would."""

    store.auto_assign(batch_id="batch-r1")
    admin, session = gateway.login("admin")
    status, publish = request_json(
        admin,
        gateway.base + "/api/admin/bundles/publish",
        body={"batch_id": "batch-r1"},
        csrf=session["csrf"],
    )
    assert status == 200 and publish["published"] >= 1, publish


def _expert_task_ids(gateway: Any, expert: Any) -> list[str]:
    with expert.open(gateway.base + "/api/expert/tasks", timeout=5) as response:
        rows = [json.loads(line) for line in response.read().decode().splitlines()]
    return [row["task_id"] for row in rows]


def test_expert_page_ships_the_parent_half_of_the_mcp_layer(
    imported_workflow: dict,
) -> None:
    with serve(imported_workflow["store"], imported_workflow["tmp"] / "state") as gateway:
        expert, session = gateway.login("expert-a")
        page, _ = gateway.read(expert, "/expert/")
        html = page.decode("utf-8")
        # Without both metas the drawer keeps Submit disabled by design.
        assert (
            '<meta name="sciencevision-trajectory-submit-endpoint" '
            'content="/sv-annotate/api/expert/trajectory">' in html
        )
        assert f'content="{session["csrf"]}"' in html
        # Local hrefs are rewritten onto the host's static routes.
        assert 'href="/mcp/' not in html and 'src="/mcp/' not in html
        for name in ("task-mcp-controls.js", "task-mcp-controls.css",
                     "task-trajectory.js", "task-trajectory.css"):
            assert f"/sv-annotate/static/mcp/{name}" in html
            body, headers = gateway.read(expert, f"/static/mcp/{name}")
            assert body == (EXPERT_APP_ROOT / "mcp" / name).read_bytes()
            assert headers["Cache-Control"] == "no-cache"

        admin, _ = gateway.login("admin")
        admin_page, _ = gateway.read(admin, "/admin/")
        assert b"sciencevision-csrf-token" not in admin_page


def test_trajectory_endpoint_requires_an_authenticated_expert(
    imported_workflow: dict,
) -> None:
    from online_harness import opener

    with serve(imported_workflow["store"], imported_workflow["tmp"] / "state") as gateway:
        url = gateway.base + "/api/expert/trajectory"
        payload = _payload()
        body = {"payload": payload, "identity": _identity(payload)}

        status, value = request_json(opener(), url, body=body)
        assert status == 401 and value["error"] == "authentication required"

        admin, admin_session = gateway.login("admin")
        status, value = request_json(admin, url, body=body, csrf=admin_session["csrf"])
        assert status == 403 and value["error"] == "expert role required"

        expert, session = gateway.login("expert-a")
        status, value = request_json(expert, url, body=body, csrf="wrong-token")
        assert status == 403 and value["error"] == "CSRF token is missing or invalid"
        status, value = request_json(
            expert, url, body=body, csrf=session["csrf"], origin=False
        )
        assert status == 403 and value["error"] == "cross-origin request rejected"


def test_trajectory_endpoint_records_and_replays_a_submission(
    imported_workflow: dict,
) -> None:
    with serve(imported_workflow["store"], imported_workflow["tmp"] / "state") as gateway:
        _publish(gateway, imported_workflow["store"])
        expert, session = gateway.login("expert-a")
        task_id = _expert_task_ids(gateway, expert)[0]
        status, manifest = request_json(expert, gateway.base + "/api/expert/manifest")
        assert status == 200
        bundle_id = manifest["bundle_id"]

        url = gateway.base + "/api/expert/trajectory"
        payload = _payload(task_id=task_id, qualified_task_id=f"{bundle_id}:{task_id}")
        identity = _identity(payload)
        body = {"payload": payload, "identity": identity}

        # The header is an alias for the payload field, never a second opinion.
        status, value = request_json(
            expert,
            url,
            body=body,
            csrf=session["csrf"],
            headers={"Idempotency-Key": "idem-other"},
        )
        assert status == 400
        assert value["error"] == "Idempotency-Key header disagrees with the payload"

        status, acknowledgment = request_json(
            expert,
            url,
            body=body,
            csrf=session["csrf"],
            headers={"Idempotency-Key": identity["idempotency_key"]},
        )
        assert status == 200, acknowledgment
        assert acknowledgment["acknowledged"] is True
        assert acknowledgment["replayed"] is False
        assert acknowledgment["submission_id"] == identity["submission_id"]

        artifact = (
            gateway.trajectory_root / bundle_id / f"{identity['submission_id']}.json"
        )
        assert json.loads(artifact.read_text(encoding="utf-8")) == payload

        status, replay = request_json(expert, url, body=body, csrf=session["csrf"])
        assert status == 200 and replay["replayed"] is True
        assert replay["payload_sha256"] == acknowledgment["payload_sha256"]

        # Trajectories are their own stream: answers are untouched.
        assert imported_workflow["store"].result_rows() == []

        # Another expert's task is out of scope even with a valid session.
        other, other_session = gateway.login("expert-b")
        status, rejected = request_json(
            other,
            url,
            body={"payload": payload, "identity": identity},
            csrf=other_session["csrf"],
        )
        assert status == 400
        assert "not in the task bundle for this expert" in rejected["error"]


def test_trajectory_endpoint_reports_unconfigured_storage(
    imported_workflow: dict,
) -> None:
    with serve(
        imported_workflow["store"],
        imported_workflow["tmp"] / "state",
        trajectories=False,
    ) as gateway:
        _publish(gateway, imported_workflow["store"])
        expert, session = gateway.login("expert-a")
        payload = _payload(task_id=_expert_task_ids(gateway, expert)[0])
        status, value = request_json(
            expert,
            gateway.base + "/api/expert/trajectory",
            body={"payload": payload, "identity": _identity(payload)},
            csrf=session["csrf"],
        )
        assert status == 503
        assert value["error"] == "trajectory storage is not configured"
