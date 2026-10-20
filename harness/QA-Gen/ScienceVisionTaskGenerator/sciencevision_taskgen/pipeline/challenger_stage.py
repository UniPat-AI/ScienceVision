"""Static quality challenge stage for generated ScienceVision tasks."""

from __future__ import annotations

import json
import logging
from typing import Any

from sciencevision_taskgen.templates import NOT_ALLOWED_AS_ANSWER

logger = logging.getLogger(__name__)

# Issue codes that are deterministically repairable by the RefinerStage.
# Defined here so RefinerStage can import them and avoid magic-string drift.
V3_MISSING_IOU_CHECK = "v3_missing_iou_check"
V6_MISSING_TOOL_AUDIT = "v6_missing_tool_audit"

# Fields that represent pose / trace process state rather than a verifiable
# scientific answer.  If *all* declared terminal_answer.fields are in this
# set the task has no verifiable terminal answer.
_PROCESS_ONLY_FIELDS: frozenset[str] = frozenset(
    {
        "azimuth",
        "elevation",
        "roll",
        "zoom",
        "trace",
        "operation_trace",
        "camera",
        "camera_state",
    }
)

# ground_truth.status values that mean "not yet verified for release"
_UNVERIFIED_GT_STATUSES: frozenset[str | None] = frozenset(
    {"seed", "needs_expert_annotation", "unverified", None, ""}
)


class ChallengerStage:
    """Checks ScienceVision task candidates for blocking design issues.

    Optional LLM semantic critique is layered on top of the existing
    rule-based checks when enabled via config.  Pass *llm_client* and
    *usage_log* to enable; when both are ``None`` (the default) output is
    byte-identical to the pre-LLM baseline.
    """

    REQUIRED_FIELDS = {
        "task_id",
        "domain",
        "v_class",
        "asset",
        "instruction",
        "required_evidence",
        "answer_schema",
        "ground_truth",
        "scorer",
        "tool_policy",
        "quality_gates",
    }

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        llm_client: Any = None,
        usage_log: Any = None,
    ) -> None:
        self.config = config or {}
        self._llm_client = llm_client
        self._usage_log = usage_log
        self._llm_enabled = bool(
            llm_client is not None
            and usage_log is not None
            and self.config.get("llm", {}).get("enabled", False)
        )

    def critique_tasks(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self._critique_with_llm(task) for task in tasks]

    def _critique_with_llm(self, task: dict[str, Any]) -> dict[str, Any]:
        """Run rule-based critique and optionally merge LLM semantic issues.

        Never raises.  When LLM is disabled returns the pure rule-based
        result unchanged.  When LLM is enabled, merges semantic issues and
        recomputes severity/recommendation.
        """
        base = self._critique_task(task)
        if not self._llm_enabled:
            return base

        llm_issues = self._llm_semantic_issues(task)
        # _llm_semantic_issues already records usage internally.
        # Merge and recompute regardless of whether fallback occurred — the
        # critique_type key is always updated to signal LLM was attempted.
        new_issues = [*base["issues"], *llm_issues]
        new_severity = self._max_severity(new_issues)
        if new_severity == "critical":
            new_recommendation = "reject"
        elif new_severity in {"high", "medium"}:
            new_recommendation = "revise"
        else:
            new_recommendation = "accept"

        return {
            **base,
            "issues": new_issues,
            "severity": new_severity,
            "recommendation": new_recommendation,
            "critique_type": "static+llm_sciencevision",
        }

    def _llm_semantic_issues(self, task: dict[str, Any]) -> list[dict[str, str]]:
        """Call the LLM reviewer and return well-formed semantic issues.

        Never raises.  Returns an empty list on any failure and records a
        fallback usage row.  Re-assigns ``self._usage_log`` after each
        ``record()`` call (UsageLog is immutable).
        """
        from sciencevision_taskgen.llm import (
            ChatClientError,
            build_challenger_messages,
            cross_model_for,
            parse_json_lenient,
            resolve_model,
        )

        task_id: str = task.get("task_id", "")
        v_class: str = task.get("v_class", "")
        reviewer_model: str = "<unresolved>"

        try:
            # Determine the builder model from provenance, if available.
            llm_meta: dict | None = (
                task.get("provenance", {}).get("llm_meta")
            )
            builder_model: str | None = None
            builder_fell_back = True
            if isinstance(llm_meta, dict):
                builder_model = llm_meta.get("model")
                builder_fell_back = bool(llm_meta.get("fallback", True))

            # Choose reviewer: cross-model when builder used a real LLM call,
            # otherwise resolve directly (no builder model to be cross from).
            if builder_model and not builder_fell_back:
                reviewer_model = cross_model_for(builder_model, self.config)
            else:
                reviewer_model = resolve_model(v_class, self.config)

            system, user = build_challenger_messages(task)
            image_paths = self._resolve_asset_image(task)
            if image_paths and hasattr(self._llm_client, "complete_vision_with_retry"):
                # Vision-grounded critique: the reviewer sees the rendered asset,
                # so ASSET_MISMATCH / SHORTCUT_RISK become real checks instead of
                # text-only guesses.
                result = self._llm_client.complete_vision_with_retry(
                    model=reviewer_model,
                    system=system,
                    user_text=user,
                    image_paths=image_paths,
                    temperature=0.2,
                )
            else:
                result = self._llm_client.complete_with_retry(
                    model=reviewer_model,
                    system=system,
                    user=user,
                    temperature=0.2,
                )
            raw_issues = parse_json_lenient(result.content)
            if not isinstance(raw_issues, list):
                raise ValueError(
                    f"LLM response is not a JSON array: {type(raw_issues).__name__}"
                )

            # Validate and prefix each issue code.
            well_formed: list[dict[str, str]] = []
            for entry in raw_issues:
                if not isinstance(entry, dict):
                    continue
                code = entry.get("code")
                severity = entry.get("severity")
                detail = entry.get("detail")
                if (
                    not isinstance(code, str)
                    or not isinstance(severity, str)
                    or not isinstance(detail, str)
                ):
                    continue
                well_formed.append(
                    self._issue(f"llm_{code}", severity, detail)
                )

        except (
            ChatClientError,
            json.JSONDecodeError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
        ) as exc:
            short_reason = repr(exc)[:200]
            self._usage_log = self._usage_log.record(
                "challenger",
                task_id,
                v_class,
                reviewer_model,
                prompt_tokens=0,
                completion_tokens=0,
                reasoning_tokens=0,
                latency_ms=0.0,
                fallback=True,
                error=short_reason,
            )
            return []

        # Success path — record usage.
        self._usage_log = self._usage_log.record(
            "challenger",
            task_id,
            v_class,
            reviewer_model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            reasoning_tokens=result.reasoning_tokens,
            latency_ms=result.latency_ms,
            fallback=False,
            error=None,
        )
        return well_formed

    def _critique_task(self, task: dict[str, Any]) -> dict[str, Any]:
        issues = []
        missing = sorted(self.REQUIRED_FIELDS - set(task))
        if missing:
            issues.append(self._issue("missing_required_fields", "critical", f"Missing fields: {missing}"))

        asset = task.get("asset", {})
        if not asset.get("path") or not asset.get("viewer") or not asset.get("format"):
            issues.append(self._issue("incomplete_asset_binding", "critical", "Asset must include path, viewer, and format."))
        if asset and not asset.get("exists_on_disk", False):
            issues.append(self._issue("asset_not_on_disk", "high", "Asset path is listed but not present on local disk."))

        v_class = task.get("v_class")
        required_evidence = set(task.get("required_evidence", []))
        scorer = task.get("scorer", {})
        tool_policy = task.get("tool_policy", {})
        answer_kind = task.get("answer_schema", {}).get("kind")

        if v_class not in {"V1", "V2", "V3", "V4", "V5", "V6"}:
            issues.append(self._issue("invalid_v_class", "critical", f"Unsupported v_class: {v_class}"))

        if not task.get("quality_gates", {}).get("irreducible_3d"):
            issues.append(self._issue("weak_3d_task", "critical", "Task is not marked as irreducible 3D."))

        if not scorer.get("primary") or not scorer.get("checks"):
            issues.append(self._issue("missing_scorer_contract", "critical", "Task must declare primary scorer and checks."))

        if v_class == "V3":
            if "screenshot" not in required_evidence or "annotation" not in required_evidence:
                issues.append(self._issue("v3_missing_visual_evidence", "critical", "V3 tasks require screenshot and annotation evidence."))
            if "annotation_iou" not in " ".join(scorer.get("checks", [])):
                issues.append(self._issue(V3_MISSING_IOU_CHECK, "high", "V3 scorer should include annotation IoU or distance."))

        if v_class == "V4":
            if "variant_screenshots" not in required_evidence:
                issues.append(self._issue("v4_missing_variants", "critical", "V4 tasks require variant screenshots."))
            if "agreement" not in answer_kind and "agreement" not in " ".join(scorer.get("checks", [])):
                issues.append(self._issue("v4_missing_agreement", "critical", "V4 tasks must score agreement."))

        if v_class == "V5":
            if answer_kind != "tool_trace":
                issues.append(self._issue("v5_not_trace_task", "critical", "V5 answer kind must be tool_trace."))
            if not task.get("trace_requirements", {}).get("max_steps"):
                issues.append(self._issue("v5_missing_step_bound", "critical", "V5 requires max_steps."))

        if v_class == "V6":
            if not tool_policy.get("forbidden_tools"):
                issues.append(self._issue("v6_missing_forbidden_tools", "critical", "V6 requires explicit forbidden analytic tools."))
            if "forbidden_tool_audit" not in scorer.get("checks", []):
                issues.append(self._issue(V6_MISSING_TOOL_AUDIT, "high", "V6 scorer should audit forbidden tool use."))

        # --- New contract blockers (Task 4) ------------------------------------

        issues.extend(self._terminal_answer_issues(task, v_class))

        # -----------------------------------------------------------------------

        if not tool_policy.get("allowed_tools"):
            issues.append(self._issue("missing_allowed_tools", "critical", "Task must declare allowed viewer/MCP tools."))

        severity = self._max_severity(issues)
        recommendation = "accept"
        if severity in {"critical"}:
            recommendation = "reject"
        elif severity in {"high", "medium"}:
            recommendation = "revise"

        return {
            "task_id": task.get("task_id"),
            "critique_type": "static_sciencevision",
            "issues": issues,
            "severity": severity,
            "recommendation": recommendation,
            "rationale": "Rule-based ScienceVision task contract review.",
        }

    def _terminal_answer_issues(
        self, task: dict[str, Any], v_class: str
    ) -> list[dict[str, str]]:
        """Return contract-blocker issues related to terminal_answer."""
        result: list[dict[str, str]] = []

        terminal_answer = task.get("terminal_answer")
        if (
            not isinstance(terminal_answer, dict)
            or not terminal_answer.get("kind")
            or not isinstance(terminal_answer.get("fields"), list)
            or not terminal_answer.get("fields")
        ):
            result.append(
                self._issue(
                    "missing_terminal_answer",
                    "critical",
                    "Task must declare terminal_answer with kind and at least one field.",
                )
            )

        gt_status = task.get("ground_truth", {}).get("status")
        if gt_status in _UNVERIFIED_GT_STATUSES:
            result.append(
                self._issue(
                    "ground_truth_not_verified",
                    "high",
                    f"ground_truth.status={gt_status!r} is not yet verified"
                    " (seed/needs_expert_annotation are not release-ready).",
                )
            )

        if isinstance(terminal_answer, dict):
            ta_kind = terminal_answer.get("kind", "")
            ta_fields: list[str] = terminal_answer.get("fields") or []
            kind_is_exempted = ta_kind in NOT_ALLOWED_AS_ANSWER and v_class in {"V1", "V5"}
            kind_forbidden = ta_kind in NOT_ALLOWED_AS_ANSWER and v_class not in {"V1", "V5"}
            # Only check process-only fields when the kind is not already
            # exempted for this v_class; if kind is allowed (e.g. V1 using
            # camera_pose_only) the associated pose fields are expected.
            fields_process_only = (
                not kind_is_exempted
                and bool(ta_fields)
                and set(ta_fields).issubset(_PROCESS_ONLY_FIELDS)
            )
            if kind_forbidden:
                result.append(
                    self._issue(
                        "answer_is_process_only",
                        "critical",
                        f"terminal_answer.kind={ta_kind!r} is not allowed as a"
                        f" verifiable answer for v_class={v_class!r}.",
                    )
                )
            elif fields_process_only:
                result.append(
                    self._issue(
                        "answer_is_process_only",
                        "critical",
                        f"All terminal_answer.fields ({ta_fields}) are pose/trace"
                        " process fields — task has no verifiable terminal answer.",
                    )
                )

        return result

    @staticmethod
    def _resolve_asset_image(task: dict[str, Any]) -> list[Any]:
        """Return [thumbnail_path] if the asset has a thumbnail present on disk.

        Empty list → the vision path is skipped and the reviewer runs text-only
        (e.g. in a fresh clone where raw assets/thumbs are pulled separately).
        """
        from sciencevision_taskgen.utils import REPO_ROOT

        thumb = (task.get("asset") or {}).get("thumb")
        if not isinstance(thumb, str) or not thumb:
            return []
        rel = thumb[1:] if thumb.startswith("/") else thumb
        path = REPO_ROOT / rel
        return [path] if path.exists() else []

    @staticmethod
    def _issue(code: str, severity: str, detail: str) -> dict[str, str]:
        return {"code": code, "severity": severity, "detail": detail}

    @staticmethod
    def _max_severity(issues: list[dict[str, str]]) -> str:
        if not issues:
            return "low"
        order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        return max((issue["severity"] for issue in issues), key=lambda item: order[item])
