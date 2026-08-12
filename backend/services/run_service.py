from __future__ import annotations

import json

from fastapi import HTTPException

from backend.schemas.runs import (
    ArtifactAvailability,
    ArtifactCounts,
    AssignmentArtifactResponse,
    ComparisonArtifactResponse,
    ComparisonRow,
    RunListResponse,
    RunManifest,
    SearchArtifactResponse,
    SummaryArtifactResponse,
    TranscriptArtifactResponse,
)
from backend.services.artifact_readers import (
    build_comparison_artifact,
    build_run_counts,
    has_comparison_source_data,
    read_assignments,
    read_summaries,
    read_transcripts,
    read_videos,
)
from backend.storage.database import epoch_of
from backend.storage.repository import RunRepository, get_repository


class RunService:
    def __init__(self, repository: RunRepository | None = None):
        self.repository = repository or get_repository()

    def get_manifest(self, run_id: str) -> RunManifest:
        run = self.repository.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
        return self._manifest_from_row(run)

    def list_runs(self) -> list[RunManifest]:
        return [self._manifest_from_row(run) for run in self.repository.list_runs()]

    def list_runs_response(self) -> RunListResponse:
        return RunListResponse(runs=self.list_runs())

    def get_latest_run(self) -> RunManifest:
        run = self.repository.latest_run()
        if run is None:
            raise HTTPException(status_code=404, detail="No pipeline runs found.")
        return self._manifest_from_row(run)

    def _manifest_from_row(self, run: dict) -> RunManifest:
        run_id = run["run_id"]
        counts = build_run_counts(self.repository, run_id)
        availability = ArtifactAvailability(
            videos=counts["videos"] > 0,
            transcripts=counts["transcripts"] > 0,
            summaries=counts["summaries"] > 0,
            comparison=has_comparison_source_data(self.repository, run_id),
            assignments=counts["assignments"] > 0,
        )
        return RunManifest(
            run_id=run_id,
            source_folder=run["source_folder"],
            search_query=run["search_query"],
            created_at=run["created_at"],
            updated_at=epoch_of(run["updated_at"]),
            is_fallback=bool(run["is_fallback"]),
            is_demo_ready=availability.videos and availability.summaries and availability.assignments,
            availability=availability,
            counts=ArtifactCounts(**counts),
        )

    def get_search_artifact(self, run_id: str) -> SearchArtifactResponse:
        run = self.get_manifest(run_id)
        return SearchArtifactResponse(
            run=run,
            search_query=run.search_query,
            timestamp=run.created_at,
            total_videos_found=len(read_videos(self.repository, run_id)),
            max_videos_requested=self.repository.get_run(run_id)["max_videos"],
            videos=read_videos(self.repository, run_id),
        )

    def get_transcripts(self, run_id: str) -> TranscriptArtifactResponse:
        run = self.get_manifest(run_id)
        return TranscriptArtifactResponse(
            run=run,
            items=read_transcripts(self.repository, run_id),
        )

    def get_summaries(self, run_id: str) -> SummaryArtifactResponse:
        run = self.get_manifest(run_id)
        return SummaryArtifactResponse(
            run=run,
            items=read_summaries(self.repository, run_id),
        )

    def get_comparison(self, run_id: str) -> ComparisonArtifactResponse:
        run = self.get_manifest(run_id)
        stored = self.repository.get_comparison(run_id)
        if stored is not None and stored["status"] == "succeeded":
            try:
                data = json.loads(stored["data"])
                rows = data.get("rows", [])
                if rows:
                    return ComparisonArtifactResponse(
                        run=run,
                        rows=[ComparisonRow(**row) for row in rows],
                        insights_report=data.get("insights_report", ""),
                        recommendations=data.get("recommendations", []),
                        used_ai_insights=bool(data.get("used_ai_insights", False)),
                    )
            except (TypeError, json.JSONDecodeError):
                pass

        rows, insights_report, recommendations = build_comparison_artifact(self.repository, run_id)
        return ComparisonArtifactResponse(
            run=run,
            rows=rows,
            insights_report=insights_report,
            recommendations=recommendations,
            used_ai_insights=False,
        )

    def get_assignments(self, run_id: str) -> AssignmentArtifactResponse:
        run = self.get_manifest(run_id)
        return AssignmentArtifactResponse(
            run=run,
            items=read_assignments(self.repository, run_id),
        )
