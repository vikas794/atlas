from __future__ import annotations

from typing import Optional

from src.application.dto.summaries import SummaryGenerationInput, SummaryGenerationOutput
from src.application.ports.provider_ports import (
    RunRepositoryPort,
    SummarizerPort,
    CachePort,
    UsageLedgerPort,
    SummaryContext,
)
from src.domain.services.hash_computer import RunHashComputer
from src.domain.exceptions import DomainError, ProviderError
from src.infrastructure.llm.openai.adapter import OpenAISummarizerAdapter
from src.infrastructure.llm.base import SettingsLoader
from src.utils import sha256_text


class GenerateSummariesUseCase:
    """Orchestrates summary generation for a pipeline run.

    This use case formalizes the summary generation orchestration previously
    embedded in backend.services.pipeline_service.PipelineService.generate_summaries().
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

    async def execute(self, input: SummaryGenerationInput) -> SummaryGenerationOutput:
        run = await self._ensure_run(input.run_id)

        settings_key = self._settings_hash(input)

        if not input.refresh:
            transcripts = [
                record
                for record in await self._repo.get_transcripts(input.run_id)
                if record["status"] == "succeeded"
            ]
            summaries = await self._repo.get_summaries(input.run_id)
            input_hash = await self._repo.transcripts_state_hash(input.run_id)
            all_fresh = (
                bool(transcripts)
                and len(summaries) == len(transcripts)
                and all(
                    record["status"] == "succeeded"
                    and record["input_hash"] == input_hash
                    and record["settings_hash"] == settings_key
                    for record in summaries
                )
            )
            if all_fresh:
                return SummaryGenerationOutput(
                    run_id=input.run_id,
                    source_folder=run["source_folder"],
                    status="cached",
                    detail="Reused existing summaries from the run folder.",
                )

        videos = await self._repo.get_videos(input.run_id)
        transcript_records = [
            record
            for record in await self._repo.get_transcripts(input.run_id)
            if record["status"] == "succeeded" and record.get("artifact_path")
        ]
        if not videos or not transcript_records:
            raise DomainError(
                "This run needs videos and transcripts before summaries can be generated."
            )

        await self._repo.start_job(input.run_id, "summaries", self._settings_dict(input))

        try:
            summarizer = OpenAISummarizerAdapter(
                settings=self._settings,
                usage_ledger=self._usage,
            )

            video_map = {v["video_id"]: v for v in videos}
            contexts = []
            for t in transcript_records:
                video = video_map.get(t["video_id"])
                if not video:
                    continue
                with open(t["artifact_path"], "r", encoding="utf-8") as f:
                    transcript_text = f.read()
                contexts.append(SummaryContext(
                    video_id=t["video_id"],
                    title=video.get("title", ""),
                    channel=video.get("channel", ""),
                    transcript_text=transcript_text,
                    language=input.transcript_language,
                    prompt_version=self._settings("prompts.summarizer.version", "v1"),
                    model=self._settings("api.openai.model", "openai/gpt-5-mini"),
                ))

            results = await summarizer.summarize_batch(contexts)

            summary_records = []
            for result in results:
                summary_records.append({
                    "video_id": result.video_id,
                    "artifact_path": result.artifact_path,
                    "content_hash": result.content_hash,
                    "byte_size": result.byte_size,
                    "data": result.summary_data,
                    "status": "succeeded",
                    "error": None,
                })

        except Exception as exc:
            await self._repo.fail_job(input.run_id, "summaries", str(exc))
            raise ProviderError(f"Summary generation failed: {exc}", provider="openai") from exc

        await self._repo.upsert_summaries(
            input.run_id,
            summary_records,
            self._settings_dict(input),
        )
        await self._repo.finish_job(input.run_id, "summaries")
        await self._repo.mark_stale_derived(input.run_id)

        return SummaryGenerationOutput(
            run_id=input.run_id,
            source_folder=run["source_folder"],
            status="updated",
            detail=f"Summary generation completed for {len(summary_records)} transcripts.",
        )

    async def _ensure_run(self, run_id: str) -> dict:
        run = await self._repo.get_run(run_id)
        if run is None:
            raise DomainError(f"Run '{run_id}' not found.")
        return run

    def _settings_dict(self, input: SummaryGenerationInput) -> dict:
        return {
            "transcript_language": input.transcript_language,
            "num_workers": input.num_workers if input.num_workers is not None else "default",
            "model": self._settings("api.openai.model", "openai/gpt-5-mini"),
        }

    def _settings_hash(self, input: SummaryGenerationInput) -> str:
        import hashlib
        import json
        payload = json.dumps(self._settings_dict(input), sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()