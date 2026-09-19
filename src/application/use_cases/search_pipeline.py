from __future__ import annotations

import time
from typing import Optional

from src.application.dto.search import SearchInput, SearchOutput
from src.application.ports.provider_ports import (
    RunRepositoryPort,
    CachePort,
    CacheKey,
    UsageLedgerPort,
)
from src.domain.services.cache_key_builder import CacheKeyBuilder
from src.domain.services.hash_computer import RunHashComputer
from src.domain.exceptions import DomainError, ProviderError
from backend.storage.cache import normalize_query
from src.infrastructure.youtube.search import YouTubeDataApiSearchProvider
from src.infrastructure.llm.base import SettingsLoader


class SearchPipelineUseCase:
    """Orchestrates YouTube video search pipeline execution.

    This use case formalizes the search orchestration previously embedded in
    backend.services.pipeline_service.PipelineService.search().
    """

    def __init__(
        self,
        run_repository: RunRepositoryPort,
        cache: CachePort,
        settings: SettingsLoader,
        usage_ledger: Optional[UsageLedgerPort] = None,
        cache_ttl_days: int = 30,
    ) -> None:
        self._repo = run_repository
        self._cache = cache
        self._settings = settings
        self._usage = usage_ledger
        self._cache_ttl_days = cache_ttl_days
        self._key_builder = CacheKeyBuilder()

    async def execute(self, input: SearchInput) -> SearchOutput:
        """Execute the search pipeline.

        Args:
            input: Search parameters including query, limits, and optional API keys.

        Returns:
            SearchOutput with run_id, status, and cache information.
        """
        normalized = normalize_query(input.query)
        cache_key = self._key_builder.search_key(
            normalized,
            input.max_videos,
            input.transcript_language,
        )

        if input.prefer_cache:
            cached = await self._repo.find_cached_run(str(cache_key))
            if cached is not None:
                await self._cache.touch(cache_key)
                return SearchOutput(
                    run_id=cached["run_id"],
                    source_folder=cached["source_folder"],
                    status="cached",
                    detail="Reused an existing cached run for the same query.",
                )

        youtube_key = None
        if not input.use_env_keys:
            if not input.youtube_api_key:
                raise DomainError(
                    "youtube_api_key is required when use_env_keys is false."
                )
            youtube_key = input.youtube_api_key

        run_id = f"pipeline_output_{int(time.time() * 1000)}"

        await self._repo.create_run(
            run_id=run_id,
            cache_key=str(cache_key),
            search_query=input.query,
            normalized_query=normalized,
            max_videos=input.max_videos,
            transcript_language=input.transcript_language,
            is_fallback=False,
        )
        await self._repo.set_run_status(run_id, "running")
        await self._repo.start_job(
            run_id,
            "search",
            {"max_videos": input.max_videos, "transcript_language": input.transcript_language},
        )

        try:
            search_provider = YouTubeDataApiSearchProvider(
                settings=self._settings,
                usage_ledger=self._usage,
                api_key=youtube_key,
            )
            videos = search_provider.search_videos(input.query, max_results=input.max_videos)
        except Exception as exc:
            await self._repo.fail_job(run_id, "search", str(exc))
            await self._repo.set_run_status(run_id, "failed", str(exc))
            raise ProviderError(f"YouTube search failed: {exc}", provider="youtube") from exc

        if not videos:
            await self._repo.fail_job(run_id, "search", "No videos found for the query.")
            await self._repo.set_run_status(run_id, "failed", "No videos found for the query.")
            raise ProviderError(
                "No YouTube videos matched this query.", provider="youtube"
            )

        video_dicts = [
            {
                "video_id": v.video_id.value,
                "title": v.title,
                "channel": v.channel,
                "url": v.url,
                "description": v.description,
                "published_at": v.published_at,
                "duration": v.duration,
            }
            for v in videos
        ]
        await self._repo.set_videos(run_id, video_dicts)

        await self._repo.finish_job(run_id, "search")
        await self._repo.set_run_status(run_id, "succeeded")

        await self._repo.put_cache_entry(
            str(cache_key),
            "search",
            run_id,
            normalized_query=normalized,
            settings={"max_videos": input.max_videos, "transcript_language": input.transcript_language},
            ttl_days=self._cache_ttl_days,
        )

        run = await self._repo.get_run(run_id)
        return SearchOutput(
            run_id=run_id,
            source_folder=run["source_folder"],
            status="created",
            detail=f"Created a new pipeline run with {len(videos)} videos.",
        )