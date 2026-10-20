"""Task builder stage for ScienceVision task specs."""

from __future__ import annotations

import json
import logging
from typing import Any

from sciencevision_taskgen.asset_facts import (
    asset_satisfies,
    lookup_facts,
    render_instruction,
    verified_entities,
)
from sciencevision_taskgen.templates import NOT_ALLOWED_AS_ANSWER, TEMPLATES, template_to_dict
from sciencevision_taskgen.utils import stable_slug

logger = logging.getLogger(__name__)


class BuilderStage:
    """Builds candidate ScienceVision tasks from planned cells.

    Optional LLM augmentation rewrites ``instruction`` and
    ``scientific_action`` per-asset when enabled via config.  Pass
    *llm_client* and *usage_log* to enable; when both are ``None`` (the
    default) output is byte-identical to the pre-LLM baseline.
    """

    def __init__(
        self,
        config: dict[str, Any],
        llm_client: Any = None,
        usage_log: Any = None,
    ) -> None:
        self.config = config
        self._llm_client = llm_client
        self._usage_log = usage_log
        self._llm_enabled = bool(
            llm_client is not None
            and usage_log is not None
            and config.get("llm", {}).get("enabled", False)
        )

    def build_tasks(self, plan: list[dict[str, Any]], catalog: dict[str, Any]) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        domains = catalog.get("domains", {})

        for item in plan:
            if item.get("status") != "ready":
                continue
            domain = item["domain"]
            v_class = item["v_class"]
            template = TEMPLATES[domain][v_class]
            assets = domains.get(domain, [])
            if not assets:
                continue
            asset = self._select_asset(
                assets, template.preferred_assets, item.get("replica", 0), template.requires
            )
            if asset is None:
                # No asset in this domain satisfies the template's grounding
                # preconditions — skip rather than emit a mis-grounded question.
                logger.info(
                    "Skipping cell %s/%s: no asset satisfies requires=%s",
                    domain, v_class, template.requires,
                )
                continue
            ordinal = len([t for t in tasks if t["domain"] == domain and t["v_class"] == v_class]) + 1
            task_id = f"sv-{stable_slug(domain)}-{v_class.lower()}-{ordinal:04d}"
            task = self._build_task(task_id, domain, v_class, asset, template, item)
            if self._llm_enabled:
                task, _meta = self._augment_with_llm(task, asset, template)
            tasks.append(task)

        if self.config.get("builder", {}).get("dedup_instructions", False):
            tasks = self._dedup_by_instruction(tasks)

        return tasks

    @staticmethod
    def _dedup_by_instruction(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Drop tasks whose instruction text duplicates an earlier one.

        Guards against low diversity when an un-grounded template renders the
        same question for several assets.  Grounded (asset-named) instructions
        differ per asset and are unaffected.
        """
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for task in tasks:
            key = task.get("instruction", "")
            if key in seen:
                logger.info("Dropping duplicate-instruction task %s", task.get("task_id"))
                continue
            seen.add(key)
            deduped.append(task)
        return deduped

    def _augment_with_llm(
        self,
        task: dict[str, Any],
        asset: dict[str, Any],
        template: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Rewrite instruction + scientific_action via the routed LLM.

        Never raises.  On any failure the original task is returned with
        ``provenance.llm_meta.fallback=True`` and a usage row is recorded.

        Returns:
            ``(updated_task, llm_meta)`` — both are new dicts (immutable
            pattern; no in-place mutation).
        """
        from sciencevision_taskgen.llm import (
            ChatClientError,
            build_builder_messages,
            parse_json_lenient,
            resolve_model,
        )

        task_id: str = task["task_id"]
        v_class: str = task["v_class"]
        model: str = "<unresolved>"

        try:
            try:
                model = resolve_model(v_class, self.config)
            except ValueError as routing_exc:
                raise ValueError(f"routing_error: {routing_exc}") from routing_exc
            system, user = build_builder_messages(task, asset, template.instruction)
            result = self._llm_client.complete_with_retry(
                model,
                system,
                user,
                temperature=self.config.get("llm", {}).get("temperature", 0.2),
                json_mode=True,
            )
            parsed = parse_json_lenient(result.content)
            if not isinstance(parsed, dict):
                raise ValueError("LLM response is not a JSON object")
            instruction = parsed.get("instruction", "")
            scientific_action = parsed.get("scientific_action", "")
            if not isinstance(instruction, str) or not instruction.strip():
                raise ValueError("missing or empty 'instruction' key")
            if not isinstance(scientific_action, str) or not scientific_action.strip():
                raise ValueError("missing or empty 'scientific_action' key")

        except (ChatClientError, json.JSONDecodeError, ValueError, KeyError) as exc:
            short_reason = repr(exc)[:200]
            llm_meta: dict[str, Any] = {
                "model": model,
                "fallback": True,
                "reason": short_reason,
            }
            self._usage_log = self._usage_log.record(
                "builder",
                task_id,
                v_class,
                model,
                prompt_tokens=0,
                completion_tokens=0,
                reasoning_tokens=0,
                latency_ms=0.0,
                fallback=True,
                error=short_reason,
            )
            updated_task = {
                **task,
                "provenance": {**task["provenance"], "llm_meta": llm_meta},
            }
            return updated_task, llm_meta

        # Success path
        rationale: str | None = parsed.get("asset_specific_rationale")
        llm_meta = {
            "model": model,
            "fallback": False,
            **({"asset_specific_rationale": rationale} if rationale is not None else {}),
        }
        self._usage_log = self._usage_log.record(
            "builder",
            task_id,
            v_class,
            model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            reasoning_tokens=result.reasoning_tokens,
            latency_ms=result.latency_ms,
            fallback=False,
            error=None,
        )
        updated_task = {
            **task,
            "instruction": instruction,
            "scientific_action": scientific_action,
            "provenance": {**task["provenance"], "llm_meta": llm_meta},
        }
        return updated_task, llm_meta

    @staticmethod
    def _select_asset(
        assets: list[dict[str, Any]],
        preferred_terms: tuple[str, ...],
        replica: int,
        requires: tuple[str, ...] = (),
    ) -> dict[str, Any] | None:
        # Only consider assets the template can be truthfully asked about.
        eligible = [a for a in assets if asset_satisfies(a, requires)]
        if not eligible:
            return None
        scored: list[tuple[int, int, dict[str, Any]]] = []
        for index, asset in enumerate(eligible):
            name = str(asset.get("name", "")).lower()
            path = str(asset.get("path", "")).lower()
            score = 0
            for term in preferred_terms:
                lowered = term.lower()
                if lowered in name or lowered in path:
                    score += 10
            if asset.get("exists_on_disk"):
                score += 1
            scored.append((-score, index, asset))
        scored.sort(key=lambda row: (row[0], row[1]))
        preferred = [row[2] for row in scored]
        return preferred[replica % len(preferred)]

    def _build_task(
        self,
        task_id: str,
        domain: str,
        v_class: str,
        asset: dict[str, Any],
        template: Any,
        plan_item: dict[str, Any],
    ) -> dict[str, Any]:
        template_dict = template_to_dict(template)
        instruction = render_instruction(template.instruction, asset)
        # A task is "grounded" when its question was bound to verified asset
        # entities via the template's requires-precondition.
        entities = verified_entities(asset, template.requires) if template.requires else []
        grounded = bool(template.requires) and bool(entities)
        # Difficulty: hard-coded from the template by default; opt-in computed
        # from the task's tool budget + cognitive load so it is not an arbitrary
        # literal.  Enable with builder.compute_difficulty=true.
        if self.config.get("builder", {}).get("compute_difficulty", False):
            difficulty = self._compute_difficulty(v_class, self._default_max_steps(v_class))
        else:
            difficulty = template.difficulty
        max_iterations = int(
            self.config.get("validation", {}).get("max_validation_iterations", 4)
        )
        scorer_expert_classes = {"V3", "V4", "V6"}
        expert_roles = (
            ["domain_expert", "scorer_expert"]
            if v_class in scorer_expert_classes
            else ["domain_expert"]
        )
        return {
            "schema_version": "1.0",
            "task_id": task_id,
            "title": template.title,
            "domain": domain,
            "v_class": v_class,
            "difficulty": difficulty,
            "asset": {
                "asset_id": asset["asset_id"],
                "name": asset["name"],
                "path": asset["path"],
                "repo_path": asset["repo_path"],
                "viewer": asset["viewer"],
                "format": asset["format"],
                "thumb": asset.get("thumb"),
                "exists_on_disk": asset.get("exists_on_disk", False),
            },
            "instruction": instruction,
            "scientific_action": template.scientific_action,
            "required_evidence": list(template.required_evidence),
            "answer_schema": {
                "kind": template.answer_kind,
                "fields": list(template.answer_fields),
            },
            "ground_truth": {
                "status": "needs_expert_annotation",
                "type": template.ground_truth_type,
                "value": None,
                "notes": "Generated seed: fill this with rendered/expert ground truth before benchmark release.",
            },
            "scorer": {
                "primary": template.scorer_primary,
                "checks": list(template.scorer_checks),
                "aggregation": "all_required_with_partial_credit",
            },
            "tool_policy": {
                "allowed_tools": list(template.allowed_tools),
                "forbidden_tools": list(template.forbidden_tools),
            },
            "trace_requirements": {
                "must_save_operation_trace": True,
                "must_include_final_state": True,
                "max_steps": self._default_max_steps(v_class),
            },
            "quality_gates": {
                "irreducible_3d": True,
                "requires_viewer": True,
                "requires_rendered_evidence": v_class in {"V1", "V2", "V3", "V4", "V5"},
                "shortcut_risk": "medium" if v_class in {"V1", "V2", "V6"} else "low",
                "irreducibility_reason": template.irreducibility_reason,
            },
            "provenance": {
                "source_manifest": "viewer/assets.json",
                "source_asset_id": asset["asset_id"],
                "generator": "ScienceVisionTaskGenerator",
                "plan_item": plan_item,
            },
            "template": template_dict,
            "terminal_answer": {
                "kind": template.terminal_answer_kind,
                "fields": list(template.terminal_answer_fields),
                "must_be_derived_from_3d_interaction": True,
                "not_allowed_as_answer": list(NOT_ALLOWED_AS_ANSWER),
            },
            "active_vision_evidence": {
                "required": list(template.required_evidence),
                "optional": ["intermediate_screenshots"],
                "role": "audit_and_secondary_scoring",
            },
            "asset_grounding": {
                "status": "grounded" if grounded else "unchecked",
                "verified_entities": entities,
                "missing_or_ambiguous_entities": [],
                "notes": (
                    "Instruction slots filled from curated asset-fact registry "
                    f"(requires={list(template.requires)})."
                    if grounded
                    else "Asset/question consistency not yet checked."
                ),
            },
            "validation_record": {
                "auto_checks": {},
                "expert_checks": {},
                "loop": {
                    "iteration": 0,
                    "max_iterations": max_iterations,
                    "status": "pending",
                },
                "release_gate": "pending",
            },
            "expert_review_requirements": {
                "roles": expert_roles,
                "min_approvals": 1,
                "review_ids": [],
            },
        }

    @staticmethod
    def _compute_difficulty(v_class: str, max_steps: int | None) -> str:
        """Derive difficulty from cognitive load (V-class) + tool budget.

        Deterministic and monotonic: inference/annotation/consistency classes
        and larger interaction budgets score harder.  Replaces the arbitrary
        per-template literal when builder.compute_difficulty is enabled.
        """
        score = 0
        if v_class in {"V3", "V4", "V5"}:
            score += 2
        elif v_class == "V6":
            score += 1
        steps = max_steps or 0
        if steps >= 12:
            score += 2
        elif steps >= 8:
            score += 1
        if score >= 3:
            return "hard"
        if score >= 1:
            return "medium"
        return "easy"

    @staticmethod
    def _default_max_steps(v_class: str) -> int | None:
        if v_class == "V5":
            return 6
        if v_class in {"V1", "V2", "V3"}:
            return 8
        if v_class == "V4":
            return 12
        return 10
