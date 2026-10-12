"""Port for persisting playlists, videos, transcripts and summaries (PRD section 7, 21).

Use cases and the worker depend on this protocol, never on the SQLite
implementation (an infrastructure adapter wired in the composition root),
so they stay testable and the storage engine stays swappable.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from src.domain.models.playlist import (
    FailureKind,
    FailureType,
    Playlist,
    Stage,
    Summary,
    Transcript,
    Video,
)
from src.domain.services.sync_diff import PlaylistEntry

VideoInput = Video | PlaylistEntry


class PlaylistRepositoryPort(Protocol):
    """Everything the pipeline needs from storage."""

    # --- playlists -------------------------------------------------------
    async def create_playlist(
        self,
        playlist_id: str,
        source_url: str,
        title: str = "",
        *,
        status: str = "ingested",
        video_count: int = 0,
    ) -> Playlist: ...

    async def get_playlist(self, playlist_pk: int) -> Playlist | None: ...

    async def get_playlist_by_youtube_id(self, playlist_id: str) -> Playlist | None: ...

    async def list_playlists(
        self, *, status: str | None = None, limit: int | None = None, offset: int = 0
    ) -> list[Playlist]: ...

    async def count_playlists(self, *, status: str | None = None) -> int: ...

    async def update_playlist(
        self,
        playlist_pk: int,
        *,
        title: str | None = None,
        status: str | None = None,
        video_count: int | None = None,
        source_url: str | None = None,
    ) -> Playlist | None: ...

    async def touch_synced(self, playlist_pk: int, synced_at: str | None = None) -> None: ...

    async def delete_playlist(self, playlist_pk: int) -> bool: ...

    async def reset_playlist(self, playlist_pk: int) -> None: ...

    # --- publishing state -----------------------------------------------
    async def begin_publish(self, playlist_pk: int) -> bool: ...

    async def finish_publish(self, playlist_pk: int, error: str | None = None) -> None: ...

    async def list_publishing_playlists(self) -> list[int]: ...

    async def set_drive_folder(self, playlist_pk: int, folder_id: str | None) -> None: ...

    async def set_drive_index_doc(self, playlist_pk: int, doc_id: str | None) -> None: ...

    async def set_pad_width(self, playlist_pk: int, width: int) -> None: ...

    async def set_index_digest(self, playlist_pk: int, digest: str | None) -> None: ...

    # --- videos ----------------------------------------------------------
    async def insert_videos(
        self, playlist_pk: int, videos: Iterable[VideoInput]
    ) -> list[Video]: ...

    async def get_video(self, video_pk: int) -> Video | None: ...

    async def list_videos(
        self,
        playlist_pk: int,
        stage: Stage | None = None,
        search: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[Video]: ...

    async def count_videos(
        self, playlist_pk: int, stage: Stage | None = None, search: str | None = None
    ) -> int: ...

    async def max_position(self, playlist_pk: int) -> int: ...

    async def count_by_stage(self, playlist_pk: int) -> dict[str, int]: ...

    async def count_published(self, playlist_pk: int) -> int: ...

    async def update_stage(
        self,
        video_pk: int,
        stage: Stage,
        *,
        failure_type: FailureType | None = None,
        failure_kind: FailureKind | None = None,
        failure_reason: str | None = None,
        next_retry_at: str | None = None,
        token: int | None = None,
        reset_retries: bool = False,
    ) -> Video | None: ...

    async def clear_failure(self, video_pk: int) -> None: ...

    async def increment_retry(
        self, video_pk: int, *, next_retry_at: str | None = None, token: int | None = None
    ) -> int: ...

    async def set_drive_doc_id(self, video_pk: int, doc_id: str | None) -> None: ...

    async def flag_removed_upstream(self, ids: Iterable[int]) -> int: ...

    async def clear_removed_upstream(self, ids: Iterable[int]) -> int: ...

    async def request_regenerate(self, video_pk: int) -> bool: ...

    # --- worker queries --------------------------------------------------
    async def list_queued_videos(
        self, playlist_pk: int | None = None, limit: int | None = None
    ) -> list[Video]: ...

    async def list_transient_due(
        self,
        playlist_pk: int | None = None,
        now_iso_str: str | None = None,
        limit: int | None = None,
    ) -> list[Video]: ...

    async def recover_in_flight_videos(self) -> int: ...

    # --- transcripts -----------------------------------------------------
    async def upsert_transcript(
        self,
        video_pk: int,
        content: str,
        *,
        language: str = "en",
        fetched_at: str | None = None,
        content_hash: str | None = None,
        byte_size: int | None = None,
        artifact_path: str | None = None,
        token: int | None = None,
    ) -> Transcript: ...

    async def get_transcript(self, video_pk: int) -> Transcript | None: ...

    async def get_transcript_artifact_paths(
        self, *, video_pk: int | None = None, playlist_pk: int | None = None
    ) -> list[str]: ...

    async def soft_delete_transcript(
        self, video_pk: int, deleted_at: str | None = None
    ) -> bool: ...

    async def soft_delete_all_transcripts(
        self, playlist_pk: int, deleted_at: str | None = None
    ) -> int: ...

    # --- summaries -------------------------------------------------------
    async def upsert_summary(
        self,
        video_pk: int,
        content: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        prompt_version: str | None = None,
        generated_at: str | None = None,
        token: int | None = None,
    ) -> Summary: ...

    async def copy_summary(
        self, source_video_pk: int, target_video_pk: int, *, token: int | None = None
    ) -> Summary | None: ...

    async def get_summary(self, video_pk: int) -> Summary | None: ...

    async def edited_video_ids(self, video_pks: Iterable[int]) -> set[int]: ...

    async def mark_edited(self, video_pk: int) -> bool: ...

    async def approve(self, video_pk: int, *, approved_at: str | None = None) -> bool: ...

    async def approve_all_pending(
        self, playlist_pk: int, *, approved_at: str | None = None
    ) -> int: ...

    async def mark_published(
        self, video_pk: int, digest: str | None = None, *, published_at: str | None = None
    ) -> bool: ...

    async def regenerate(
        self,
        video_pk: int,
        content: str,
        provider: str | None = None,
        model: str | None = None,
        prompt_version: str | None = None,
        *,
        generated_at: str | None = None,
        token: int | None = None,
    ) -> Summary: ...
