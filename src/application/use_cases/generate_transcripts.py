from __future__ import annotations

from typing import Optional

from src.application.dto.transcripts import TranscriptGenerationInput, TranscriptGenerationOutput
from src.application.ports.provider_ports import (
    RunRepositoryPort,
    TranscriptProviderPort,
    CachePort,
    CacheKey,
    UsageLedgerPort,
)
from src.domain.services.cache_key_builder import CacheKeyBuilder
from src.domain.services.hash_computer import RunHashComputer
from src.domain.exceptions import DomainError, ProviderError
from src.infrastructure.transcript.ytdlp.provider import YtDlpTranscriptProvider
from src.infrastructure.llm.base import SettingsLoader
from src.utils import sha256_text


class GenerateTranscriptsUseCase:
    """Orchestrates transcript fetching for a pipeline run.

    This use case formalizes the transcript generation orchestration previously
    embedded in backend.services.pipeline_service.PipelineService.generate_transcripts().
    """

    def __init__(
        self,
        run_repository: RunRepositoryPort,
        cache: CachePort,
        settings: SettingsLoader,
        usage_ledger: Optional[UsageLedgerPort] = None,
        artifact_store: Optional[object] = None,
    ) -> None:
        self._repo = run_repository
        self._cache = cache
        self._settings = settings
        self._usage = usage_ledger
        self._artifact_store = artifact_store
        self._key_builder = CacheKeyBuilder()

    async def execute(self, input: TranscriptGenerationInput) -> TranscriptGenerationOutput:
        run = await self._ensure_run(input.run_id)

        settings_key = self._settings_hash(input)

        if not input.refresh:
            videos = await self._repo.get_videos(input.run_id)
            existing = await self._repo.get_transcripts(input.run_id)
            input_hash = await self._repo.videos_state_hash(input.run_id)
            all_fresh = (
                bool(videos)
                and len(existing) == len(videos)
                and all(
                    record["status"] == "succeeded"
                    and record["input_hash"] == input_hash
                    and record["settings_hash"] == settings_key
                    for record in existing
                )
            )
            if all_fresh:
                return TranscriptGenerationOutput(
                    run_id=input.run_id,
                    source_folder=run["source_folder"],
                    status="cached",
                    detail="Reused existing transcripts from the run folder.",
                    transcripts_available=len(existing),
                )

        videos = await self._repo.get_videos(input.run_id)
        if not videos:
            raise DomainError("This run does not have video metadata to fetch transcripts.")

        await self._repo.start_job(input.run_id, "transcripts", self._settings_dict(input))

        try:
            transcript_provider = YtDlpTranscriptProvider(
                settings=self._settings,
                usage_ledger=self._usage,
                artifact_store=self._artifact_store,
            )
            video_ids = [v["video_id"] for v in videos]
            transcript_results = await transcript_provider.fetch_transcripts(
                video_ids, language=input.transcript_language
            )

            transcript_records = []
            successful_paths = []
            for idx, video in enumerate(videos):
                video_id = video["video_id"]
                result = transcript_results[idx]
                transcript_records.append({
                    "video_id": video_id,
                    "language": result.language,
                    "artifact_path": result.artifact_path,
                    "content_hash": result.content_hash,
                    "byte_size": result.byte_size,
                    "status": "succeeded" if result.raw_srt else "failed",
                    "error": None if result.raw_srt else "No transcript available",
                })
                if result.raw_srt:
                    successful_paths.append(result.artifact_path)

        except Exception as exc:
            await self._repo.fail_job(input.run_id, "transcripts", str(exc))
            raise ProviderError(f"Transcript generation failed: {exc}", provider="ytdlp") from exc

        skipped_counts = {}
        for record in transcript_records:
            if record["status"] != "succeeded":
                skipped_counts[record["error"] or "unknown"] = skipped_counts.get(record["error"] or "unknown", 0) + 1

        detail = f"Transcript generation completed for {len(successful_paths)} of {len(videos)} videos."
        if skipped_counts:
            summary = ", ".join(f"{key}: {count}" for key, count in skipped_counts.items())
            detail += f" Skipped {sum(skipped_counts.values())} ({summary})."

        await self._repo.upsert_transcripts(
            input.run_id,
            transcript_records,
            self._settings_dict(input),
        )
        await self._repo.finish_job(input.run_id, "transcripts")
        await self._repo.mark_stale_derived(input.run_id)

        return TranscriptGenerationOutput(
            run_id=input.run_id,
            source_folder=run["source_folder"],
            status="updated",
            detail=detail,
            transcripts_available=len(successful_paths),
        )

    async def _ensure_run(self, run_id: str) -> dict:
        run = await self._repo.get_run(run_id)
        if run is None:
            raise DomainError(f"Run '{run_id}' not found.")
        return run

    def _settings_dict(self, input: TranscriptGenerationInput) -> dict:
        return {
            "transcript_language": input.transcript_language,
            "num_workers": input.num_workers if input.num_workers is not None else "default",
        }

    def _settings_hash(self, input: TranscriptGenerationInput) -> str:
        import hashlib
        import json
        payload = json.dumps(self._settings_dict(input), sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()