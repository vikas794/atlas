from __future__ import annotations

import json
from typing import Optional

from src.application.dto.comparison import ComparisonGenerationInput, ComparisonGenerationOutput
from src.application.ports.provider_ports import (
    RunRepositoryPort,
    InsightsProviderPort,
    CachePort,
    UsageLedgerPort,
)
from src.domain.services.comparison_inference import ComparisonInferenceService
from src.domain.services.hash_computer import RunHashComputer
from src.domain.exceptions import DomainError, ProviderError
from src.infrastructure.llm.openai.adapter import OpenAIInsightsProvider
from src.infrastructure.llm.base import SettingsLoader
from src.services.artifact_readers import build_comparison_artifact


class GenerateComparisonUseCase:
    """Orchestrates comparison generation for a pipeline run.

    This use case formalizes the comparison orchestration previously
    embedded in backend.services.pipeline_service.PipelineService.generate_comparison().
    """

    def __init__(
        self,
        run_repository: RunRepositoryPort,
        cache: CachePort,
        settings: SettingsLoader,
        usage_ledger: Optional[UsageLedgerPort] = None,
    ) -> None:
        self._repo = run_repository
        self._cache = cache
        self._settings = settings
        self._usage = usage_ledger

    async def execute(self, input: ComparisonGenerationInput) -> ComparisonGenerationOutput:
        run = await self._ensure_run(input.run_id)

        settings_key = self._settings_hash(input)

        if not input.refresh and not input.use_ai_insights:
            stored = await self._repo.get_comparison(input.run_id)
            input_hash = await self._repo.derived_input_hash(input.run_id)
            if (
                stored is not None
                and stored["status"] == "succeeded"
                and stored["input_hash"] == input_hash
                and stored["settings_hash"] == settings_key
            ):
                return ComparisonGenerationOutput(
                    run_id=input.run_id,
                    source_folder=run["source_folder"],
                    status="cached",
                    detail="Comparison will be derived from cached summaries and metadata.",
                )

        await self._repo.start_job(input.run_id, "comparison", {"use_ai_insights": input.use_ai_insights})

        try:
            rows, insights_report, recommendations = build_comparison_artifact(
                self._repo, input.run_id
            )

            if input.use_ai_insights:
                insights_provider = OpenAIInsightsProvider(
                    settings=self._settings,
                    usage_ledger=self._usage,
                )
                video_metadata = await self._repo.get_videos(input.run_id)
                summaries = await self._repo.get_summaries(input.run_id)
                summary_data = {}
                for record in summaries:
                    if record["status"] == "succeeded" and record.get("data"):
                        try:
                            summary_data[record["video_id"]] = json.loads(record["data"])
                        except (TypeError, json.JSONDecodeError):
                            pass

                video_meta_list = [video_metadata] if not isinstance(video_metadata, list) else video_metadata
                summary_list = [summary_data[vm.get("video_id", "")] for vm in video_meta_list if vm.get("video_id") in summary_data]

                if video_meta_list and summary_list:
                    ai_insights = await insights_provider.generate_insights_batch(
                        video_meta_list,
                        summary_list,
                        prompt_version=self._settings("prompts.insights.version", "v1"),
                        model=self._settings("api.openai.model", "openai/gpt-5-mini"),
                    )
                    for i, insight in enumerate(ai_insights):
                        if i < len(rows):
                            rows[i] = rows[i]._replace(
                                difficulty=insight.difficulty_level,
                                teaching_style=insight.teaching_style,
                                practical_value=insight.practical_value,
                                content_depth=insight.content_depth,
                                target_audience=insight.target_audience,
                                key_differentiators=insight.key_differentiators,
                                worth_time=insight.time_investment_worth,
                                prerequisites=insight.prerequisites,
                                learning_outcome=insight.learning_outcome,
                                follow_up_recommendations=insight.follow_up_recommendations,
                            )

            await self._repo.set_comparison(
                input.run_id,
                {
                    "rows": [r.__dict__ if hasattr(r, '__dict__') else r for r in rows],
                    "insights_report": insights_report,
                    "recommendations": recommendations,
                    "used_ai_insights": input.use_ai_insights,
                },
                {"use_ai_insights": input.use_ai_insights},
            )

        except Exception as exc:
            await self._repo.fail_job(input.run_id, "comparison", str(exc))
            raise ProviderError(f"Comparison generation failed: {exc}", provider="openai") from exc

        await self._repo.finish_job(input.run_id, "comparison")

        return ComparisonGenerationOutput(
            run_id=input.run_id,
            source_folder=run["source_folder"],
            status="updated",
            detail="Comparison analysis was refreshed for this run.",
        )

    async def _ensure_run(self, run_id: str) -> dict:
        run = await self._repo.get_run(run_id)
        if run is None:
            raise DomainError(f"Run '{run_id}' not found.")
        return run

    def _settings_hash(self, input: ComparisonGenerationInput) -> str:
        import hashlib
        import json
        payload = json.dumps({"use_ai_insights": input.use_ai_insights}, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()