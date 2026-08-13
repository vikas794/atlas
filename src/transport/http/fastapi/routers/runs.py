from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from src.application.artifact_readers import (
    build_comparison_artifact,
    build_run_counts,
    has_comparison_source_data,
    read_assignments,
    read_summaries,
    read_transcripts,
    read_videos,
)
from src.application.ports.provider_ports import RunRepositoryPort
from src.transport.http.fastapi.schemas.runs import (
    AssignmentArtifactResponse,
    ComparisonArtifactResponse,
    RunListResponse,
    RunManifest,
    SearchArtifactResponse,
    SummaryArtifactResponse,
    TranscriptArtifactResponse,
)
from src.transport.http.fastapi.dependencies import get_run_repository

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _manifest_from_row(repository: RunRepositoryPort, run: dict) -> RunManifest:
    run_id = run["run_id"]
    counts = build_run_counts(repository, run_id)
    availability = {
        "videos": counts["videos"] > 0,
        "transcripts": counts["transcripts"] > 0,
        "summaries": counts["summaries"] > 0,
        "comparison": has_comparison_source_data(repository, run_id),
        "assignments": counts["assignments"] > 0,
    }
    updated_at = run["updated_at"]
    if hasattr(updated_at, 'timestamp'):
        updated_at = updated_at.timestamp()
    return RunManifest(
        run_id=run_id,
        source_folder=run["source_folder"],
        search_query=run["search_query"],
        created_at=run["created_at"],
        updated_at=updated_at,
        is_fallback=bool(run["is_fallback"]),
        is_demo_ready=availability["videos"] and availability["summaries"] and availability["assignments"],
        availability=availability,
        counts=counts,
    )


@router.get("", response_model=RunListResponse)
def list_runs(repository: RunRepositoryPort = Depends(get_run_repository)) -> RunListResponse:
    runs = [_manifest_from_row(repository, run) for run in repository.list_runs()]
    return RunListResponse(runs=runs)


@router.get("/latest", response_model=RunManifest)
def get_latest_run(repository: RunRepositoryPort = Depends(get_run_repository)) -> RunManifest:
    run = repository.latest_run()
    if run is None:
        raise HTTPException(status_code=404, detail="No pipeline runs found.")
    return _manifest_from_row(repository, run)


@router.get("/{run_id}", response_model=RunManifest)
def get_run_manifest(run_id: str, repository: RunRepositoryPort = Depends(get_run_repository)) -> RunManifest:
    run = repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    return _manifest_from_row(repository, run)


@router.get("/{run_id}/videos", response_model=SearchArtifactResponse)
def get_run_videos(run_id: str, repository: RunRepositoryPort = Depends(get_run_repository)) -> SearchArtifactResponse:
    run = _manifest_from_row(repository, repository.get_run(run_id))
    videos = read_videos(repository, run_id)
    return SearchArtifactResponse(
        run=run,
        search_query=run.search_query,
        timestamp=run.created_at,
        total_videos_found=len(videos),
        max_videos_requested=repository.get_run(run_id)["max_videos"],
        videos=videos,
    )


@router.get("/{run_id}/transcripts", response_model=TranscriptArtifactResponse)
def get_run_transcripts(run_id: str, repository: RunRepositoryPort = Depends(get_run_repository)) -> TranscriptArtifactResponse:
    run = _manifest_from_row(repository, repository.get_run(run_id))
    transcripts = read_transcripts(repository, run_id)
    return TranscriptArtifactResponse(
        run=run,
        items=transcripts,
    )


@router.get("/{run_id}/summaries", response_model=SummaryArtifactResponse)
def get_run_summaries(run_id: str, repository: RunRepositoryPort = Depends(get_run_repository)) -> SummaryArtifactResponse:
    run = _manifest_from_row(repository, repository.get_run(run_id))
    summaries = read_summaries(repository, run_id)
    return SummaryArtifactResponse(
        run=run,
        items=summaries,
    )


@router.get("/{run_id}/comparison", response_model=ComparisonArtifactResponse)
def get_run_comparison(run_id: str, repository: RunRepositoryPort = Depends(get_run_repository)) -> ComparisonArtifactResponse:
    run = _manifest_from_row(repository, repository.get_run(run_id))
    stored = repository.get_comparison(run_id)
    if stored is not None and stored["status"] == "succeeded":
        try:
            import json
            data = json.loads(stored["data"])
            rows = data.get("rows", [])
            if rows:
                from src.application.dto.comparison import ComparisonRow
                comparison_rows = [ComparisonRow(**row) for row in rows]
                return ComparisonArtifactResponse(
                    run=run,
                    rows=comparison_rows,
                    insights_report=data.get("insights_report", ""),
                    recommendations=data.get("recommendations", []),
                    used_ai_insights=bool(data.get("used_ai_insights", False)),
                )
        except (TypeError, json.JSONDecodeError):
            pass

    rows, insights_report, recommendations = build_comparison_artifact(repository, run_id)
    return ComparisonArtifactResponse(
        run=run,
        rows=rows,
        insights_report=insights_report,
        recommendations=recommendations,
        used_ai_insights=False,
    )


@router.get("/{run_id}/assignments", response_model=AssignmentArtifactResponse)
def get_run_assignments(run_id: str, repository: RunRepositoryPort = Depends(get_run_repository)) -> AssignmentArtifactResponse:
    run = _manifest_from_row(repository, repository.get_run(run_id))
    assignments = read_assignments(repository, run_id)
    return AssignmentArtifactResponse(
        run=run,
        items=assignments,
    )