"""Seed planning for domain x V-class coverage."""

from __future__ import annotations

from typing import Any

from sciencevision_taskgen.templates import CANONICAL_DOMAINS, EXTENSION_DOMAINS, TEMPLATES, V_CLASSES


class PlannerStage:
    """Creates a deterministic seed plan from catalog coverage and config."""

    def __init__(self, config: dict[str, Any]):
        self.config = config

    def plan(self, catalog: dict[str, Any]) -> list[dict[str, Any]]:
        domains = self.config.get("target_domains") or CANONICAL_DOMAINS
        if self.config.get("include_extension_domains", False):
            domains = list(dict.fromkeys([*domains, *EXTENSION_DOMAINS]))

        v_classes = self.config.get("v_classes") or V_CLASSES
        tasks_per_cell = int(self.config.get("tasks_per_cell", 1))
        max_tasks = self.config.get("max_tasks")

        plan: list[dict[str, Any]] = []
        domains_in_catalog = catalog.get("domains", {})
        for domain in domains:
            assets = domains_in_catalog.get(domain, [])
            if not assets:
                plan.append({
                    "domain": domain,
                    "status": "skipped",
                    "reason": "no local viewer-ready assets in catalog",
                    "asset_count": 0,
                })
                continue
            if domain not in TEMPLATES:
                plan.append({
                    "domain": domain,
                    "status": "pending_adapter",
                    "reason": "assets exist but no domain templates are implemented yet",
                    "asset_count": len(assets),
                })
                continue
            for v_class in v_classes:
                if v_class not in TEMPLATES[domain]:
                    plan.append({
                        "domain": domain,
                        "v_class": v_class,
                        "status": "skipped",
                        "reason": "no template for this domain/V-class cell",
                        "asset_count": len(assets),
                    })
                    continue
                for replica in range(tasks_per_cell):
                    plan.append({
                        "domain": domain,
                        "v_class": v_class,
                        "replica": replica,
                        "status": "ready",
                        "asset_count": len(assets),
                    })
                    if max_tasks and len([p for p in plan if p.get("status") == "ready"]) >= int(max_tasks):
                        return plan
        return plan
