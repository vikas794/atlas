"""FastAPI router for playlist management endpoints (PRD section 16)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from src.application.use_cases.playlist.actions import (
    ApproveAllUseCase,
    CleanupTranscriptsUseCase,
    DeletePlaylistUseCase,
    ReprocessPlaylistUseCase,
    RetryAllFailedUseCase,
)
from src.application.use_cases.playlist.ingest import IngestPlaylistUseCase
from src.application.use_cases.playlist.publish import PublishPlaylistUseCase
from src.application.worker import PlaylistWorker
from src.domain.exceptions import NotFoundError
from src.domain.interfaces.playlist_repository import PlaylistRepositoryPort
from src.domain.models.playlist import Playlist, Stage
from src.transport.http.fastapi.dependencies import (
    get_approve_all_use_case,
    get_cleanup_transcripts_use_case,
    get_delete_playlist_use_case,
    get_ingest_playlist_use_case,
    get_playlist_repo,
    get_publish_playlist_use_case,
    get_reprocess_playlist_use_case,
    get_retry_all_failed_use_case,
    get_worker,
)
from src.transport.http.fastapi.schemas.playlists import (
    ActionResponse,
    IngestPlaylistRequest,
    PlaylistListResponse,
    PlaylistResponse,
    PublishResponse,
    VideoListResponse,
    VideoResponse,
)

router = APIRouter(prefix="/api/playlists", tags=["playlists"])

RepoDep = Annotated[PlaylistRepositoryPort, Depends(get_playlist_repo)]
WorkerDep = Annotated[PlaylistWorker, Depends(get_worker)]
IngestDep = Annotated[IngestPlaylistUseCase, Depends(get_ingest_playlist_use_case)]
ReprocessDep = Annotated[ReprocessPlaylistUseCase, Depends(get_reprocess_playlist_use_case)]
DeleteDep = Annotated[DeletePlaylistUseCase, Depends(get_delete_playlist_use_case)]
ApproveAllDep = Annotated[ApproveAllUseCase, Depends(get_approve_all_use_case)]
RetryAllDep = Annotated[RetryAllFailedUseCase, Depends(get_retry_all_failed_use_case)]
CleanupDep = Annotated[CleanupTranscriptsUseCase, Depends(get_cleanup_transcripts_use_case)]
PublishDep = Annotated[PublishPlaylistUseCase, Depends(get_publish_playlist_use_case)]


def _not_found(playlist_id: int) -> NotFoundError:
    return NotFoundError(f"Playlist {playlist_id} not found")


async def _to_playlist_response(
    repo: PlaylistRepositoryPort, playlist: Playlist
) -> PlaylistResponse:
    counts = await repo.count_by_stage(playlist.id)
    return PlaylistResponse(
        id=playlist.id,
        playlist_id=playlist.playlist_id,
        source_url=playlist.source_url,
        title=playlist.title,
        video_count=playlist.video_count,
        status=playlist.status,
        drive_folder_id=playlist.drive_folder_id,
        drive_index_doc_id=playlist.drive_index_doc_id,
        created_at=playlist.created_at,
        updated_at=playlist.updated_at,
        last_synced_at=playlist.last_synced_at,
        stage_counts=counts,
        last_publish_error=playlist.last_publish_error,
    )


@router.post(
    "",
    response_model=PlaylistResponse,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": PlaylistResponse, "description": "Known playlist: re-synced"}},
)
async def ingest_playlist(
    request: IngestPlaylistRequest,
    response: Response,
    use_case: IngestDep,
    repo: RepoDep,
    worker: WorkerDep,
) -> PlaylistResponse:
    """Submit a YouTube playlist URL: 201 for a new playlist, 200 when a known one was re-synced."""
    result = await use_case.ingest(request.url)
    if not result.created:
        response.status_code = status.HTTP_200_OK
    worker.wake()
    return await _to_playlist_response(repo, result.playlist)


@router.get("", response_model=PlaylistListResponse)
async def list_playlists(
    repo: RepoDep,
    limit: int | None = Query(None, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> PlaylistListResponse:
    """List tracked playlists newest-first with per-stage progress counts."""
    playlists = await repo.list_playlists(limit=limit, offset=offset)
    items = [await _to_playlist_response(repo, p) for p in playlists]
    return PlaylistListResponse(playlists=items, total=await repo.count_playlists())


@router.get("/{playlist_id}", response_model=PlaylistResponse)
async def get_playlist(
    playlist_id: int,
    repo: RepoDep,
) -> PlaylistResponse:
    """Get a playlist with stage counts by internal primary key."""
    playlist = await repo.get_playlist(playlist_id)
    if playlist is None:
        raise _not_found(playlist_id)
    return await _to_playlist_response(repo, playlist)


@router.get("/{playlist_id}/videos", response_model=VideoListResponse)
async def list_playlist_videos(
    playlist_id: int,
    repo: RepoDep,
    stage: Stage | None = Query(None, description="Filter by stage"),
    search: str | None = Query(None, description="Filter by title or video id"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
) -> VideoListResponse:
    """List videos in a playlist with optional stage and search filtering.

    An unknown ``stage`` value is rejected with 422 by the framework.
    """
    playlist = await repo.get_playlist(playlist_id)
    if playlist is None:
        raise _not_found(playlist_id)

    videos = await repo.list_videos(
        playlist_id,
        stage=stage,
        search=search,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    total = await repo.count_videos(playlist_id, stage=stage, search=search)
    edited_ids = await repo.edited_video_ids(v.id for v in videos)
    items = [VideoResponse.from_domain(v) for v in videos]
    for item in items:
        item.edited = item.id in edited_ids
    return VideoListResponse(
        videos=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/{playlist_id}/process",
    response_model=ActionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def process_playlist(
    playlist_id: int,
    repo: RepoDep,
    worker: WorkerDep,
) -> ActionResponse:
    """Start or resume background processing for queued videos in this playlist."""
    playlist = await repo.get_playlist(playlist_id)
    if playlist is None:
        raise _not_found(playlist_id)

    worker.wake()
    return ActionResponse(status="accepted", message="Playlist processing triggered")


@router.post(
    "/{playlist_id}/reprocess",
    response_model=ActionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reprocess_playlist(
    playlist_id: int,
    use_case: ReprocessDep,
    worker: WorkerDep,
) -> ActionResponse:
    """Reset playlist to scratch (hard-delete notes/transcripts) and requeue all videos.

    Drive folder / Doc ids are kept, so re-publishing updates the existing Docs in place.
    """
    success = await use_case.execute(playlist_id)
    if not success:
        raise _not_found(playlist_id)
    worker.wake()
    return ActionResponse(status="accepted", message="Playlist queued for full reprocessing")


@router.delete("/{playlist_id}", response_model=ActionResponse)
async def delete_playlist(
    playlist_id: int,
    use_case: DeleteDep,
) -> ActionResponse:
    """Permanently delete a playlist and all associated data."""
    deleted = await use_case.execute(playlist_id)
    if not deleted:
        raise _not_found(playlist_id)
    return ActionResponse(status="ok", message="Playlist deleted successfully")


@router.post("/{playlist_id}/retry-failed", response_model=ActionResponse)
async def retry_failed_videos(
    playlist_id: int,
    use_case: RetryAllDep,
    worker: WorkerDep,
) -> ActionResponse:
    """Re-queue every failed video of this playlist (skips removed-upstream ones)."""
    count = await use_case.execute(playlist_id)
    if count:
        worker.wake()
    return ActionResponse(status="ok", message=f"Queued {count} videos for retry", count=count)


@router.post("/{playlist_id}/approve-all", response_model=ActionResponse)
async def approve_all_videos(
    playlist_id: int,
    use_case: ApproveAllDep,
) -> ActionResponse:
    """Approve all videos in pending_review stage for this playlist."""
    approved_count = await use_case.execute(playlist_id)
    return ActionResponse(
        status="ok",
        message=f"Approved {approved_count} videos",
        count=approved_count,
    )


@router.delete("/{playlist_id}/transcripts", response_model=ActionResponse)
async def cleanup_transcripts(
    playlist_id: int,
    repo: RepoDep,
    use_case: CleanupDep,
) -> ActionResponse:
    """Clear all stored transcripts of a playlist (text and raw subtitle files)."""
    if await repo.get_playlist(playlist_id) is None:
        raise _not_found(playlist_id)
    count = await use_case.execute_playlist(playlist_id)
    return ActionResponse(
        status="ok",
        message=f"Cleared {count} transcripts",
        count=count,
    )


@router.post(
    "/{playlist_id}/publish",
    response_model=PublishResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def publish_playlist(
    playlist_id: int,
    use_case: PublishDep,
    worker: WorkerDep,
) -> PublishResponse:
    """Queue publishing of approved notes to Google Drive and return immediately (PRD section 16).

    The work runs in the background worker. Poll ``GET /api/playlists/{id}``: ``status`` is
    ``publishing`` while it runs, then ``published`` or ``publish_failed`` (with
    ``last_publish_error``). Returns 409 ``DRIVE_AUTH_REQUIRED`` when Drive is not authorized
    and 409 ``CONFLICT`` when a publish is already running.
    """
    playlist = await use_case.request_publish(playlist_id)
    worker.enqueue_publish(playlist_id)
    return PublishResponse(
        folder_id=playlist.drive_folder_id or "",
        index_doc_id=playlist.drive_index_doc_id,
        status="accepted",
    )
