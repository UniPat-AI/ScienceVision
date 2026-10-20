"""Command-line runner for the ScienceVision task generation pipeline."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

from sciencevision_taskgen.pipeline.builder_stage import BuilderStage
from sciencevision_taskgen.pipeline.catalog_stage import CatalogStage
from sciencevision_taskgen.pipeline.challenger_stage import ChallengerStage
from sciencevision_taskgen.pipeline.export_stage import ExportStage
from sciencevision_taskgen.pipeline.planner_stage import PlannerStage
from sciencevision_taskgen.pipeline.refiner_stage import RefinerStage
from sciencevision_taskgen.utils import create_run_dir, load_json

logger = logging.getLogger(__name__)


class ScienceVisionPipelineRunner:
    """Orchestrates the ScienceVision asset-to-task generation pipeline."""

    def __init__(self, config_path: str = "configs/default_pipeline.json"):
        self.config_path = config_path
        self.config = load_json(config_path)
        self.catalog_stage = CatalogStage(
            assets_path=self.config.get("assets_path", "viewer/assets.json"),
            matched_index_path=self.config.get("matched_index_path", "datasets/_matched/index.json"),
        )
        self.planner_stage = PlannerStage(self.config)
        self.refiner_stage = RefinerStage(self.config)
        self.export_stage = ExportStage()

        # Optional LLM wiring.
        # ChatClient has no run-dir dependency so it is safe to build here.
        # UsageLog needs the run_dir (created inside run()), so it is built
        # lazily inside run() after create_run_dir().  When llm.enabled is True
        # we defer BuilderStage and ChallengerStage construction to run() as
        # well so both stages receive a fully-initialised UsageLog.
        self._llm_client: Any = None
        self._llm_enabled: bool = bool(self.config.get("llm", {}).get("enabled", False))

        if self._llm_enabled:
            self._llm_client = self._build_chat_client()
            if self._llm_client is None:
                # Credential or config failure: fall back to deterministic path.
                self._llm_enabled = False

        if not self._llm_enabled:
            # Deterministic path: construct stages once here, byte-identical to
            # the pre-LLM baseline.
            self.builder_stage = BuilderStage(self.config)
            self.challenger_stage = ChallengerStage()
        # When _llm_enabled is True, builder_stage / challenger_stage are set
        # inside run() once the UsageLog is ready.

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_chat_client(self) -> Any | None:
        """Construct a ChatClient from config and env credentials.

        Returns None (and logs a warning) on any failure so __init__ can
        gracefully fall back to the deterministic path without crashing.
        """
        try:
            # VisionChatClient is a drop-in superset of ChatClient (adds
            # complete_vision*), so the builder path is unaffected while the
            # challenger can send the asset render when a thumbnail exists.
            from sciencevision_taskgen.llm._vision import VisionChatClient
            from sciencevision_taskgen.llm.routing import load_credentials

            base_url, api_key = load_credentials()
            timeout_sec = float(self.config.get("llm", {}).get("timeout_seconds", 60))
            return VisionChatClient(base_url=base_url, api_key=api_key, timeout_sec=timeout_sec)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "LLM wiring disabled: could not build ChatClient (%s). "
                "Falling back to deterministic pipeline.",
                exc,
            )
            return None

    def _fill_ground_truth(
        self, tasks: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Run the GroundTruthStage over accepted tasks (opt-in).

        Returns the (possibly) enriched tasks and a small report dict.  Never
        raises: any stage failure logs and returns the tasks unchanged.
        """
        try:
            from sciencevision_taskgen.pipeline.groundtruth_stage import GroundTruthStage
            from sciencevision_taskgen.utils import REPO_ROOT

            filled, report = GroundTruthStage(repo_root=REPO_ROOT).fill_tasks(tasks)
            return filled, {
                "total": report.total,
                "auto_seed": report.auto_seed,
                "awaiting_expert": report.awaiting_expert,
                "needs_expert_annotation": report.needs_expert_annotation,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ground-truth fill skipped (%s).", exc)
            return tasks, {"error": repr(exc)[:200]}

    def _build_usage_log(self, run_dir: Path) -> Any:
        """Construct a UsageLog for run_dir using pricing from config."""
        from sciencevision_taskgen.llm.usage import UsageLog

        pricing = self.config.get("llm", {}).get("pricing_per_mtok", {})
        return UsageLog.for_run(run_dir, pricing)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        run_dir = create_run_dir(self.config.get("runs_dir", "data/runs"))

        if self._llm_enabled:
            # Build UsageLog now that run_dir exists, then wire stages.
            usage_log = self._build_usage_log(Path(run_dir))
            builder_stage = BuilderStage(
                self.config,
                llm_client=self._llm_client,
                usage_log=usage_log,
            )
            challenger_stage = ChallengerStage(
                config=self.config,
                llm_client=self._llm_client,
                usage_log=usage_log,
            )
        else:
            builder_stage = self.builder_stage
            challenger_stage = self.challenger_stage

        catalog = self.catalog_stage.build_catalog()
        plan = self.planner_stage.plan(catalog)
        candidates = builder_stage.build_tasks(plan, catalog)
        max_iter = int(self.config.get("validation", {}).get("max_validation_iterations", 4))
        final_tasks, decisions, critiques = self.refiner_stage.refine_tasks(
            candidates, challenger_stage, max_iter
        )

        # Optional ground-truth fill (opt-in).  The default pipeline emits seed
        # specs with ground_truth.status="needs_expert_annotation"; enabling
        # this runs the registered fillers (LLM-as-judge over the asset
        # thumbnail for the auto-fillable types).  Off by default → baseline
        # output unchanged.
        gt_report: dict[str, Any] | None = None
        if self.config.get("groundtruth", {}).get("enabled", False):
            final_tasks, gt_report = self._fill_ground_truth(final_tasks)

        outputs = self.export_stage.export(
            run_dir=run_dir,
            catalog=catalog,
            plan=plan,
            candidates=candidates,
            critiques=critiques,
            decisions=decisions,
            final_tasks=final_tasks,
        )
        return {
            "catalog_summary": catalog.get("summary", {}),
            "planned": len(plan),
            "candidates": len(candidates),
            "accepted": len(final_tasks),
            "ground_truth": gt_report,
            "outputs": outputs,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate ScienceVision 3D QA task seeds.")
    parser.add_argument(
        "--config",
        default="configs/default_pipeline.json",
        help="Path to pipeline config, relative to ScienceVisionTaskGenerator or repo root.",
    )
    args = parser.parse_args()

    result = ScienceVisionPipelineRunner(args.config).run()
    print("ScienceVision task generation complete.")
    print(f"  Planned seeds:    {result['planned']}")
    print(f"  Candidate tasks:  {result['candidates']}")
    print(f"  Accepted tasks:   {result['accepted']}")
    print(f"  Run directory:    {result['outputs']['run_dir']}")
    print(f"  Final JSONL:      {result['outputs']['final_jsonl']}")
    print(f"  Report:           {result['outputs']['report']}")


if __name__ == "__main__":
    main()
