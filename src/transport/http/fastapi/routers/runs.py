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


async def _manifest_from_row(repository: RunRepositoryPort, run: dict) -> RunManifest:
    run_id = run["run_id"]
    counts = await build_run_counts(repository, run_id)
    availability = {
        "videos": counts["videos"] > 0,
        "transcripts": counts["transcripts"] > 0,
        "summaries": counts["summaries"] > 0,
        "comparison": await has_comparison_source_data(repository, run_id),
        "assignments": counts["assignments"] > 0,
    }
    updated_at = run["updated_at"]
    if hasattr(updated_at, 'timestamp'):
        updated_at = updated_at.timestamp()
    elif isinstance(updated_at, str):
        # Parse ISO string to timestamp
        from datetime import datetime
        updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00")).timestamp()
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
async def list_runs(repository: RunRepositoryPort = Depends(get_run_repository)) -> RunListResponse:
    runs_data = await repository.list_runs()
    runs = [await _manifest_from_row(repository, run) for run in runs_data]
    return RunListResponse(runs=runs)


@router.get("/latest", response_model=RunManifest)
async def get_latest_run(repository: RunRepositoryPort = Depends(get_run_repository)) -> RunManifest:
    run = await repository.latest_run()
    if run is None:
        raise HTTPException(status_code=404, detail="No pipeline runs found.")
    return await _manifest_from_row(repository, run)


@router.get("/{run_id}", response_model=RunManifest)
async def get_run_manifest(run_id: str, repository: RunRepositoryPort = Depends(get_run_repository)) -> RunManifest:
    run = await repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    return await _manifest_from_row(repository, run)


async def _get_run_or_404(repository: RunRepositoryPort, run_id: str) -> dict:
    run = await repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    return run


@router.get("/{run_id}/videos", response_model=SearchArtifactResponse)
async def get_run_videos(run_id: str, repository: RunRepositoryPort = Depends(get_run_repository)) -> SearchArtifactResponse:
    run_data = await _get_run_or_404(repository, run_id)
    run = await _manifest_from_row(repository, run_data)
    videos = await read_videos(repository, run_id)
    return SearchArtifactResponse(
        run=run,
        search_query=run.search_query,
        timestamp=run.created_at,
        total_videos_found=len(videos),
        max_videos_requested=run_data["max_videos"],
        videos=videos,
    )


@router.get("/{run_id}/transcripts", response_model=TranscriptArtifactResponse)
async def get_run_transcripts(run_id: str, repository: RunRepositoryPort = Depends(get_run_repository)) -> TranscriptArtifactResponse:
    run_data = await _get_run_or_404(repository, run_id)
    run = await _manifest_from_row(repository, run_data)
    transcripts = await read_transcripts(repository, run_id)
    return TranscriptArtifactResponse(
        run=run,
        items=transcripts,
    )


@router.get("/{run_id}/summaries", response_model=SummaryArtifactResponse)
async def get_run_summaries(run_id: str, repository: RunRepositoryPort = Depends(get_run_repository)) -> SummaryArtifactResponse:
    run_data = await _get_run_or_404(repository, run_id)
    run = await _manifest_from_row(repository, run_data)
    summaries = await read_summaries(repository, run_id)
    return SummaryArtifactResponse(
        run=run,
        items=summaries,
    )


@router.get("/{run_id}/comparison", response_model=ComparisonArtifactResponse)
async def get_run_comparison(run_id: str, repository: RunRepositoryPort = Depends(get_run_repository)) -> ComparisonArtifactResponse:
    run_data = await _get_run_or_404(repository, run_id)
    run = await _manifest_from_row(repository, run_data)
    stored = await repository.get_comparison(run_id)
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

    rows, insights_report, recommendations = await build_comparison_artifact(repository, run_id)
    return ComparisonArtifactResponse(
        run=run,
        rows=rows,
        insights_report=insights_report,
        recommendations=recommendations,
        used_ai_insights=False,
    )


@router.get("/{run_id}/assignments", response_model=AssignmentArtifactResponse)
async def get_run_assignments(run_id: str, repository: RunRepositoryPort = Depends(get_run_repository)) -> AssignmentArtifactResponse:
    run_data = await _get_run_or_404(repository, run_id)
    run = await _manifest_from_row(repository, run_data)
    assignments = await read_assignments(repository, run_id)
    return AssignmentArtifactResponse(
        run=run,
        items=assignments,
    )