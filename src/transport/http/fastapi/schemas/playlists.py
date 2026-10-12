"""Pydantic schemas for the playlist pipeline API (PRD section 16)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, Field

from src.config import load_settings
from src.domain.models.playlist import Summary, Video
from src.shared.text import sha256_text


@lru_cache(maxsize=1)
def _max_auto_retries() -> int:
    """Configured automatic-retry limit, so the UI can show "attempt 3/5"."""
    return int(load_settings().max_auto_retries)


class IngestPlaylistRequest(BaseModel):
    """Payload to ingest or re-sync a YouTube playlist."""

    url: str = Field(..., description="YouTube playlist URL or watch URL containing &list=")


class PlaylistResponse(BaseModel):
    """Representation of a tracked playlist."""

    id: int
    playlist_id: str
    source_url: str
    title: str
    video_count: int
    status: str
    drive_folder_id: str | None = None
    drive_index_doc_id: str | None = None
    created_at: str
    updated_at: str
    last_synced_at: str | None = None
    stage_counts: dict[str, int] = Field(default_factory=dict)
    #: Publishing runs in the background: ``status`` moves ``publishing`` -> ``published`` /
    #: ``publish_failed`` and the redacted reason of a failed publish is here.
    last_publish_error: str | None = None


class PlaylistListResponse(BaseModel):
    """List of playlists with total count."""

    playlists: list[PlaylistResponse]
    total: int


class VideoResponse(BaseModel):
    """Representation of a playlist video."""

    id: int
    playlist_id: int
    youtube_video_id: str
    title: str
    position: int
    stage: str
    failure_type: str | None = None
    failure_kind: str | None = None
    failure_reason: str | None = None
    retry_count: int = 0
    next_retry_at: str | None = None
    drive_doc_id: str | None = None
    removed_upstream: bool = False
    created_at: str
    updated_at: str
    #: True while a transient failure is waiting for its scheduled automatic retry.
    auto_retry: bool = False
    #: True when a transient failure used up its automatic retries: manual retry only
    #: (show "Failed - temporary" without implying more retries are coming).
    retries_exhausted: bool = False
    #: Primary key of the first occurrence when this row repeats an earlier video.
    duplicate_of: int | None = None
    #: True while a "regenerate notes" request is queued or running.
    regenerating: bool = False
    #: Configured automatic-retry limit (denominator for "attempt N/M").
    max_auto_retries: int | None = None
    #: True when the notes were hand-edited (drives the list's "Edited" tag).
    edited: bool = False

    @classmethod
    def from_domain(cls, video: Video) -> VideoResponse:
        return cls(
            id=video.id,
            playlist_id=video.playlist_id,
            youtube_video_id=video.youtube_video_id,
            title=video.title,
            position=video.position,
            stage=video.stage.value,
            failure_type=video.failure_type.value if video.failure_type else None,
            failure_kind=video.failure_kind.value if video.failure_kind else None,
            failure_reason=video.failure_reason,
            retry_count=video.retry_count,
            next_retry_at=video.next_retry_at,
            drive_doc_id=video.drive_doc_id,
            removed_upstream=video.removed_upstream,
            created_at=video.created_at,
            updated_at=video.updated_at,
            auto_retry=video.auto_retry,
            retries_exhausted=video.retries_exhausted,
            duplicate_of=video.duplicate_of,
            regenerating=video.regen_requested,
            max_auto_retries=_max_auto_retries(),
        )


class VideoListResponse(BaseModel):
    """Paginated list of videos."""

    videos: list[VideoResponse]
    total: int
    page: int = 1
    page_size: int = 50


class SummaryResponse(BaseModel):
    """Generated notes for a video."""

    id: int
    video_id: int
    content: str
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    generated_at: str
    edited: bool = False
    approved: bool = False
    approved_at: str | None = None
    published: bool = False
    published_at: str | None = None
    published_digest: str | None = None
    #: True when the notes were edited after they were published (Re-publish needed).
    needs_republish: bool = False

    @classmethod
    def from_domain(cls, summary: Summary) -> SummaryResponse:
        return cls(
            id=summary.id,
            video_id=summary.video_id,
            content=summary.content,
            provider=summary.provider,
            model=summary.model,
            prompt_version=summary.prompt_version,
            generated_at=summary.generated_at,
            edited=summary.edited,
            approved=summary.approved,
            approved_at=summary.approved_at,
            published=summary.published,
            published_at=summary.published_at,
            published_digest=summary.published_digest,
            needs_republish=bool(
                summary.published
                and summary.edited
                and sha256_text(summary.content) != summary.published_digest
            ),
        )


class SaveSummaryRequest(BaseModel):
    """Request payload to save inline summary edit."""

    content: str = Field(..., description="Markdown content for the summary")


class ActionResponse(BaseModel):
    """Generic response for pipeline commands."""

    status: str = "ok"
    message: str
    count: int | None = None


class PublishResponse(BaseModel):
    """Acknowledgement of a publish request (HTTP 202).

    Publishing runs in the background worker: poll ``GET /api/playlists/{id}`` and watch
    ``status`` (``publishing`` -> ``published`` / ``publish_failed``) and ``last_publish_error``.
    The counts are 0 here; ``folder_id`` / ``index_doc_id`` are whatever is stored so far.
    """

    folder_id: str = ""
    index_doc_id: str | None = None
    created: int = 0
    updated: int = 0
    skipped: int = 0
    status: str = "accepted"
