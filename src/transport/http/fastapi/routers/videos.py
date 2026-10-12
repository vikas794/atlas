"""FastAPI router for video-level operations and review (PRD section 16)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from src.application.use_cases.playlist.actions import (
    ApproveVideoUseCase,
    CleanupTranscriptsUseCase,
    RegenerateVideoUseCase,
    RetryVideoUseCase,
    SaveSummaryEditUseCase,
)
from src.application.worker import PlaylistWorker
from src.domain.exceptions import NotFoundError
from src.domain.interfaces.playlist_repository import PlaylistRepositoryPort
from src.transport.http.fastapi.dependencies import (
    get_approve_video_use_case,
    get_cleanup_transcripts_use_case,
    get_playlist_repo,
    get_regenerate_video_use_case,
    get_retry_video_use_case,
    get_save_summary_edit_use_case,
    get_worker,
)
from src.transport.http.fastapi.schemas.playlists import (
    ActionResponse,
    SaveSummaryRequest,
    SummaryResponse,
    VideoResponse,
)

router = APIRouter(prefix="/api/videos", tags=["videos"])

RepoDep = Annotated[PlaylistRepositoryPort, Depends(get_playlist_repo)]
WorkerDep = Annotated[PlaylistWorker, Depends(get_worker)]
ApproveDep = Annotated[ApproveVideoUseCase, Depends(get_approve_video_use_case)]
RetryDep = Annotated[RetryVideoUseCase, Depends(get_retry_video_use_case)]
RegenDep = Annotated[RegenerateVideoUseCase, Depends(get_regenerate_video_use_case)]
SaveEditDep = Annotated[SaveSummaryEditUseCase, Depends(get_save_summary_edit_use_case)]
CleanupDep = Annotated[CleanupTranscriptsUseCase, Depends(get_cleanup_transcripts_use_case)]


@router.get("/{video_id}", response_model=VideoResponse)
async def get_video(video_id: int, repo: RepoDep) -> VideoResponse:
    """Fetch one video (stage, failure details, retry state); the UI polls this."""
    video = await repo.get_video(video_id)
    if video is None:
        raise NotFoundError(f"Video {video_id} not found")
    return VideoResponse.from_domain(video)


@router.get("/{video_id}/summary", response_model=SummaryResponse)
async def get_video_summary(
    video_id: int,
    repo: RepoDep,
) -> SummaryResponse:
    """Fetch the generated summary notes for a video."""
    summary = await repo.get_summary(video_id)
    if summary is None:
        raise NotFoundError("Summary not found for video")
    return SummaryResponse.from_domain(summary)


@router.put("/{video_id}/summary", response_model=SummaryResponse)
async def update_video_summary(
    video_id: int,
    request: SaveSummaryRequest,
    use_case: SaveEditDep,
) -> SummaryResponse:
    """Save user edits to the summary notes (409 unless notes exist and are reviewable)."""
    summary = await use_case.execute(video_id, request.content)
    return SummaryResponse.from_domain(summary)


@router.post("/{video_id}/approve", response_model=VideoResponse)
async def approve_video(
    video_id: int,
    use_case: ApproveDep,
) -> VideoResponse:
    """Approve video summary, moving state to approved (409 when there is nothing to approve)."""
    video = await use_case.execute(video_id)
    return VideoResponse.from_domain(video)


@router.post("/{video_id}/retry", response_model=VideoResponse)
async def retry_video(
    video_id: int,
    use_case: RetryDep,
    worker: WorkerDep,
) -> VideoResponse:
    """Manually retry a failed video."""
    video = await use_case.execute(video_id)
    worker.wake()
    return VideoResponse.from_domain(video)


@router.post(
    "/{video_id}/regenerate",
    response_model=VideoResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def regenerate_video(
    video_id: int,
    use_case: RegenDep,
    worker: WorkerDep,
) -> VideoResponse:
    """Queue a regeneration of the notes and return immediately (PRD section 16).

    The video goes back to ``queued`` with ``regenerating = true``; the worker reuses the
    stored transcript (or re-fetches it if it was cleaned up), generates new notes and
    returns the video to ``pending_review``. Poll ``GET /api/videos/{id}``, then fetch
    ``/summary``.
    """
    video = await use_case.execute(video_id)
    worker.wake()
    return VideoResponse.from_domain(video)


@router.delete("/{video_id}/transcript", response_model=ActionResponse)
async def delete_video_transcript(
    video_id: int,
    repo: RepoDep,
    use_case: CleanupDep,
) -> ActionResponse:
    """Clear the stored transcript of a single video (text and raw subtitle file)."""
    if await repo.get_video(video_id) is None:
        raise NotFoundError(f"Video {video_id} not found")
    if not await use_case.execute_video(video_id):
        raise NotFoundError("No active transcript found")
    return ActionResponse(status="ok", message="Transcript cleared")
