"""Asset catalog ingestion for ScienceVision task generation."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from sciencevision_taskgen.utils import REPO_ROOT, load_json


class CatalogStage:
    """Loads local ScienceVision asset manifests and matched QA inventory."""

    def __init__(
        self,
        assets_path: str = "viewer/assets.json",
        matched_index_path: str = "datasets/_matched/index.json",
    ):
        self.assets_path = assets_path
        self.matched_index_path = matched_index_path

    def build_catalog(self) -> dict[str, Any]:
        assets_raw = load_json(self.assets_path)
        matched_index = self._load_optional_json(self.matched_index_path)

        domains: dict[str, list[dict[str, Any]]] = {}
        flat_assets: list[dict[str, Any]] = []

        for domain, rows in assets_raw.items():
            if not isinstance(rows, list):
                continue
            normalized = []
            for idx, row in enumerate(rows):
                item = self._normalize_asset(domain, idx, row)
                normalized.append(item)
                flat_assets.append(item)
            domains[domain] = normalized

        return {
            "schema_version": "1.0",
            "source_manifests": {
                "assets": self.assets_path,
                "matched_index": self.matched_index_path if matched_index else None,
            },
            "domains": domains,
            "matched_qa": matched_index,
            "summary": self._summarize(domains, matched_index),
            "flat_assets": flat_assets,
        }

    def _normalize_asset(self, domain: str, idx: int, row: dict[str, Any]) -> dict[str, Any]:
        path = row.get("path", "")
        repo_path = path[1:] if isinstance(path, str) and path.startswith("/") else path
        full_path = REPO_ROOT / repo_path if repo_path else None
        return {
            "asset_id": f"{domain}:{idx:04d}",
            "domain": domain,
            "name": row.get("name", f"{domain}_{idx:04d}"),
            "path": path,
            "repo_path": repo_path,
            "exists_on_disk": bool(full_path and full_path.exists()),
            "viewer": row.get("viewer"),
            "format": row.get("format"),
            "size": row.get("size"),
            "thumb": row.get("thumb"),
            "source_manifest_index": idx,
        }

    @staticmethod
    def _load_optional_json(path: str) -> dict[str, Any] | None:
        full = REPO_ROOT / path
        if not full.exists():
            return None
        return load_json(path)

    @staticmethod
    def _summarize(
        domains: dict[str, list[dict[str, Any]]],
        matched_index: dict[str, Any] | None,
    ) -> dict[str, Any]:
        by_domain = {domain: len(items) for domain, items in sorted(domains.items())}
        viewers = Counter()
        formats = Counter()
        missing = 0
        for items in domains.values():
            for item in items:
                viewers[item.get("viewer") or "unknown"] += 1
                formats[item.get("format") or "unknown"] += 1
                if not item.get("exists_on_disk"):
                    missing += 1
        return {
            "asset_count": sum(by_domain.values()),
            "domain_count": len(by_domain),
            "assets_by_domain": by_domain,
            "assets_by_viewer": dict(sorted(viewers.items())),
            "assets_by_format": dict(sorted(formats.items())),
            "missing_on_disk": missing,
            "matched_qa_total": (matched_index or {}).get("total", 0),
        }


def group_by_domain(assets: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for asset in assets:
        grouped[asset["domain"]].append(asset)
    return dict(grouped)
