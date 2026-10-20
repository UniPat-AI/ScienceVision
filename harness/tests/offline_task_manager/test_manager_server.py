from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tools.offline_task_manager.server import Handler, ManagerServer
from tools.offline_task_manager.store import ManagerStore


def test_manager_is_loopback_only_and_serves_same_store(tmp_path: Path) -> None:
    store = ManagerStore(tmp_path / "manager.db")
    server = ManagerServer(("127.0.0.1", 0), Handler)
    server.store = store
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert server.server_address[0] == "127.0.0.1"
        with urllib.request.urlopen(base + "/", timeout=3) as response:
            page = response.read()
            # The page is Chinese-first; its English wording now lives in
            # the language table, so assert on the translation key instead.
            assert b'data-i18n="manager.eyebrow"' in page
            assert b'admission-receipt' in page
            assert response.headers["Content-Security-Policy"]
        request = urllib.request.Request(
            base + "/api/experts/import",
            data=json.dumps(
                {"csv": "expert_id,name,domains\nexpert-http,HTTP Expert,biology\n"}
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            assert json.load(response)["imported"] == 1
        with urllib.request.urlopen(base + "/api/overview", timeout=3) as response:
            assert json.load(response)["experts"][0]["expert_id"] == "expert-http"
        with urllib.request.urlopen(base + "/api/results/export?format=json", timeout=3) as response:
            exported = json.load(response)
        assert exported["schema_version"] == "sciencevision.manager_results_export.v2"
        assert exported["contract_version"] == "sciencevision.manager_results_exchange.v2"
        assert exported["exchange_mode"] == "mixed-v1-v2"
        assert exported["results"] == []
        missing_admission = urllib.request.Request(
            base + "/api/batches/import",
            data=json.dumps(
                {
                    "batch_id": "batch-http",
                    "tasks_jsonl": str(tmp_path / "missing.jsonl"),
                    "asset_root": str(tmp_path),
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(missing_admission, timeout=3)
        assert error.value.code == 400
        assert "QC admission receipt is required" in error.value.read().decode()
        assert store.overview()["batches"] == []
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(base + "/%2e%2e/store.py", timeout=3)
        assert error.value.code == 404
        forbidden_host = urllib.request.Request(base + "/api/overview", headers={"Host": "evil.test"})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(forbidden_host, timeout=3)
        assert error.value.code == 403
        wrong_type = urllib.request.Request(
            base + "/api/experts/import",
            data=b"{}",
            headers={"Content-Type": "text/plain"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(wrong_type, timeout=3)
        assert error.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        store.close()
