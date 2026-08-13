from __future__ import annotations

import json
import os
import time

from fastapi import HTTPException

from backend.schemas.pipeline import (
    ArtifactGenerationRequest,
    PipelineActionResponse,
    SearchRequest,
)
from backend.services.artifact_readers import build_comparison_artifact
from backend.services.run_service import RunService
from backend.storage.cache import cache_key, normalize_query, settings_hash
from backend.storage.repository import RunRepository, get_repository
from backend.storage.settings import get_settings
from src.utils import get_config, get_worker_count


class PipelineService:
    def __init__(
        self,
        run_service: RunService | None = None,
        repository: RunRepository | None = None,
        openrouter_api_key: str | None = None,
        youtube_api_key: str | None = None,
    ):
        self.repository = repository or get_repository()
        self.run_service = run_service or RunService(self.repository)
        self._openrouter_api_key = openrouter_api_key
        self._youtube_api_key = youtube_api_key

    def _apply_api_keys(self, request: SearchRequest) -> tuple[str | None, str | None]:
        if request.use_env_keys:
            return (
                self._openrouter_api_key or os.getenv("OPENROUTER_API_KEY"),
                self._youtube_api_key or os.getenv("YOUTUBE_API_KEY"),
            )
        if not request.openrouter_api_key or not request.youtube_api_key:
            raise HTTPException(
                status_code=400,
                detail="Both openrouter_api_key and youtube_api_key are required when use_env_keys is false.",
            )
        return request.openrouter_api_key, request.youtube_api_key

    @staticmethod
    def _settings_for(request: ArtifactGenerationRequest) -> dict:
        return {
            "transcript_language": request.transcript_language,
            "num_workers": request.num_workers if request.num_workers is not None else "default",
        }

    @staticmethod
    def _assignment_settings(request: ArtifactGenerationRequest) -> dict:
        return {
            "num_workers": get_worker_count(request.num_workers),
            "model": get_config("api.openai.model", "openai/gpt-4o-mini"),
        }

    def search(self, request: SearchRequest) -> PipelineActionResponse:
        normalized = normalize_query(request.query)
        key = cache_key("search", normalized, request.max_videos, request.transcript_language)

        if request.prefer_cache:
            cached = self.repository.find_cached_run(key)
            if cached is not None:
                self.repository.touch_hit(key)
                return PipelineActionResponse(
                    run_id=cached["run_id"],
                    source_folder=cached["source_folder"],
                    status="cached",
                    detail="Reused an existing cached run for the same query.",
                )

        openrouter_key, youtube_key = self._apply_api_keys(request)

        run_id = f"pipeline_output_{int(time.time() * 1000)}"
        self.repository.create_run(
            run_id=run_id,
            cache_key=key,
            search_query=request.query,
            normalized_query=normalized,
            max_videos=request.max_videos,
            transcript_language=request.transcript_language,
        )
        self.repository.set_run_status(run_id, "running")
        self.repository.start_job(
            run_id,
            "search",
            {"max_videos": request.max_videos, "transcript_language": request.transcript_language},
        )

        try:
            from src.youtube_pipeline import YouTubePipeline

            pipeline = YouTubePipeline(
                repository=self.repository,
                run_id=run_id,
                max_videos=request.max_videos,
                transcript_language=request.transcript_language,
                num_workers=request.num_workers,
                openrouter_api_key=openrouter_key,
                youtube_api_key=youtube_key,
            )
            videos = pipeline.search_videos(request.query)
        except Exception as exc:
            self.repository.fail_job(run_id, "search", str(exc))
            self.repository.set_run_status(run_id, "failed", str(exc))
            raise HTTPException(
                status_code=502,
                detail=f"YouTube search failed: {exc}",
            ) from exc

        if not videos:
            self.repository.fail_job(run_id, "search", "No videos found for the query.")
            self.repository.set_run_status(run_id, "failed", "No videos found for the query.")
            raise HTTPException(
                status_code=404,
                detail="No YouTube videos matched this query.",
            )

        self.repository.finish_job(run_id, "search")
        self.repository.set_run_status(run_id, "succeeded")
        settings = get_settings()
        self.repository.put_cache_entry(
            key,
            "search",
            run_id,
            normalized_query=normalized,
            settings={"max_videos": request.max_videos, "transcript_language": request.transcript_language},
            ttl_days=settings["cache_ttl_days"],
        )

        run = self.repository.get_run(run_id)
        return PipelineActionResponse(
            run_id=run_id,
            source_folder=run["source_folder"],
            status="created",
            detail=f"Created a new pipeline run with {len(videos)} videos.",
        )

    def generate_transcripts(self, run_id: str, request: ArtifactGenerationRequest) -> PipelineActionResponse:
        run = self._ensure_run(run_id)
        settings_key = settings_hash(self._settings_for(request))

        if not request.refresh:
            videos = self.repository.get_videos(run_id)
            existing = self.repository.get_transcripts(run_id)
            input_hash = self.repository.videos_state_hash(run_id)
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
                return PipelineActionResponse(
                    run_id=run_id,
                    source_folder=run["source_folder"],
                    status="cached",
                    detail="Reused existing transcripts from the run folder.",
                    transcripts_available=len(existing),
                )

        videos = self.repository.get_videos(run_id)
        if not videos:
            raise HTTPException(status_code=400, detail="This run does not have video metadata to fetch transcripts.")

        self.repository.start_job(run_id, "transcripts", self._settings_for(request))
        try:
            from src.youtube_pipeline import YouTubePipeline

            pipeline = YouTubePipeline(
                repository=self.repository,
                run_id=run_id,
                max_videos=len(videos),
                transcript_language=request.transcript_language,
                num_workers=request.num_workers,
            )
            transcript_paths, _ = pipeline.fetch_transcripts(videos)
        except Exception as exc:
            self.repository.fail_job(run_id, "transcripts", str(exc))
            raise HTTPException(
                status_code=502,
                detail=f"Transcript generation failed: {exc}",
            ) from exc

        skipped_counts: dict[str, int] = {}
        for status in getattr(pipeline.transcript_fetcher, "statuses", {}).values():
            if status != "success":
                skipped_counts[status] = skipped_counts.get(status, 0) + 1
        detail = f"Transcript generation completed for {len(transcript_paths)} of {len(videos)} videos."
        if skipped_counts:
            summary = ", ".join(f"{key}: {count}" for key, count in skipped_counts.items())
            detail += f" Skipped {sum(skipped_counts.values())} ({summary})."

        self.repository.finish_job(run_id, "transcripts")
        self.repository.mark_stale_derived(run_id)
        return PipelineActionResponse(
            run_id=run_id,
            source_folder=run["source_folder"],
            status="updated",
            detail=detail,
            transcripts_available=len(transcript_paths),
        )

    def generate_summaries(self, run_id: str, request: ArtifactGenerationRequest) -> PipelineActionResponse:
        run = self._ensure_run(run_id)
        settings_key = settings_hash(self._settings_for(request))

        if not request.refresh:
            transcripts = [
                record
                for record in self.repository.get_transcripts(run_id)
                if record["status"] == "succeeded"
            ]
            summaries = self.repository.get_summaries(run_id)
            input_hash = self.repository.transcripts_state_hash(run_id)
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
                return PipelineActionResponse(
                    run_id=run_id,
                    source_folder=run["source_folder"],
                    status="cached",
                    detail="Reused existing summaries from the run folder.",
                )

        videos = self.repository.get_videos(run_id)
        transcript_paths = [
            record["artifact_path"]
            for record in self.repository.get_transcripts(run_id)
            if record["status"] == "succeeded" and record.get("artifact_path")
        ]
        if not videos or not transcript_paths:
            raise HTTPException(status_code=400, detail="This run needs videos and transcripts before summaries can be generated.")

        self.repository.start_job(run_id, "summaries", self._settings_for(request))
        try:
            from src.youtube_pipeline import YouTubePipeline

            pipeline = YouTubePipeline(
                repository=self.repository,
                run_id=run_id,
                max_videos=len(videos),
                transcript_language=request.transcript_language,
                num_workers=request.num_workers,
            )
            result = pipeline.summarize_transcripts(transcript_paths, videos)
        except Exception as exc:
            self.repository.fail_job(run_id, "summaries", str(exc))
            raise HTTPException(
                status_code=502,
                detail=f"Summary generation failed: {exc}",
            ) from exc

        self.repository.finish_job(run_id, "summaries")
        self.repository.mark_stale_derived(run_id)
        return PipelineActionResponse(
            run_id=run_id,
            source_folder=run["source_folder"],
            status="updated",
            detail=f"Summary generation completed for {sum(result.values())} transcripts.",
        )

    def generate_comparison(self, run_id: str, request: ArtifactGenerationRequest) -> PipelineActionResponse:
        run = self._ensure_run(run_id)
        settings_key = settings_hash({"use_ai_insights": request.use_ai_insights})

        if not request.refresh and not request.use_ai_insights:
            stored = self.repository.get_comparison(run_id)
            input_hash = self.repository.derived_input_hash(run_id)
            if (
                stored is not None
                and stored["status"] == "succeeded"
                and stored["input_hash"] == input_hash
                and stored["settings_hash"] == settings_key
            ):
                return PipelineActionResponse(
                    run_id=run_id,
                    source_folder=run["source_folder"],
                    status="cached",
                    detail="Comparison will be derived from cached summaries and metadata.",
                )

        self.repository.start_job(run_id, "comparison", {"use_ai_insights": request.use_ai_insights})
        try:
            if request.use_ai_insights:
                from src.compare_youtube_outputs import YouTubeOutputComparator

                comparator = YouTubeOutputComparator(
                    repository=self.repository,
                    run_id=run_id,
                    use_ai_insights=True,
                    num_workers=request.num_workers,
                )
                comparator.run_comparison(fix_json=False, save_detailed=False)

            rows, insights_report, recommendations = build_comparison_artifact(self.repository, run_id)
            self.repository.set_comparison(
                run_id,
                {
                    "rows": [row.model_dump() for row in rows],
                    "insights_report": insights_report,
                    "recommendations": recommendations,
                    "used_ai_insights": request.use_ai_insights,
                },
                {"use_ai_insights": request.use_ai_insights},
            )
        except HTTPException:
            self.repository.fail_job(run_id, "comparison", "Comparison generation failed.")
            raise
        except Exception as exc:
            self.repository.fail_job(run_id, "comparison", str(exc))
            raise HTTPException(
                status_code=502,
                detail=f"Comparison generation failed: {exc}",
            ) from exc

        self.repository.finish_job(run_id, "comparison")
        return PipelineActionResponse(
            run_id=run_id,
            source_folder=run["source_folder"],
            status="updated",
            detail="Comparison analysis was refreshed for this run.",
        )

    def generate_assignments(self, run_id: str, request: ArtifactGenerationRequest) -> PipelineActionResponse:
        run = self._ensure_run(run_id)
        assignment_settings = self._assignment_settings(request)
        settings_key = settings_hash(assignment_settings)

        if not request.refresh:
            assignments = self.repository.get_assignments(run_id)
            input_hash = self.repository.derived_input_hash(run_id)
            all_fresh = (
                bool(assignments)
                and all(
                    record["status"] == "succeeded"
                    and record["input_hash"] == input_hash
                    and record["settings_hash"] == settings_key
                    for record in assignments
                )
            )
            if all_fresh:
                return PipelineActionResponse(
                    run_id=run_id,
                    source_folder=run["source_folder"],
                    status="cached",
                    detail="Reused existing assignments from the run folder.",
                )

        video_metadata = {video["video_id"]: video for video in self.repository.get_videos(run_id)}
        summary_data: dict[str, dict] = {}
        for record in self.repository.get_summaries(run_id):
            if record["status"] != "succeeded":
                continue
            try:
                summary_data[record["video_id"]] = json.loads(record["data"])
            except (TypeError, json.JSONDecodeError):
                continue

        self.repository.start_job(run_id, "assignments", assignment_settings)
        try:
            from src.assignment_generator import YouTubeAssignmentGenerator

            generator = YouTubeAssignmentGenerator(
                repository=self.repository,
                run_id=run_id,
                num_workers=request.num_workers,
            )
            results = generator.generate_assignments(video_metadata, summary_data)
        except Exception as exc:
            self.repository.fail_job(run_id, "assignments", str(exc))
            raise HTTPException(
                status_code=502,
                detail=f"Assignment generation failed: {exc}",
            ) from exc

        self.repository.finish_job(run_id, "assignments")
        return PipelineActionResponse(
            run_id=run_id,
            source_folder=run["source_folder"],
            status="updated",
            detail=f"Assignment generation completed for {sum(results.values())} summaries.",
        )

    def _ensure_run(self, run_id: str) -> dict:
        run = self.repository.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
        return run
