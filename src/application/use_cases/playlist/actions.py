"""Playlist and video user actions use cases (PRD section 15, section 16).

Covers:
- ApproveVideo: approve a single pending video.
- ApproveAll: approve all pending_review videos in a playlist.
- SaveSummaryEdit: persist user edits to notes, marking edited flag.
- RetryVideo: manual retry for failed videos, clearing failure and resetting retry budget.
- RegenerateVideo: queue a regenerate of the notes; the worker does the slow part.
- ReprocessPlaylist: full reprocess, resetting videos to queued.
- DeletePlaylist: cascade deletion of playlist and associated data.
- CleanupTranscripts: clear stored transcripts (text and raw subtitle files) for a video/playlist.

Invalid requests raise ``NotFoundError`` (404) or ``ConflictError`` / ``InvalidTransitionError``
(409) so the HTTP layer can answer precisely.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable

from src.domain.exceptions import ConflictError, NotFoundError
from src.domain.interfaces.playlist_repository import PlaylistRepositoryPort
from src.domain.models.playlist import Stage, Summary, Video
from src.domain.services.stage_machine import assert_transition, can_regenerate
from src.shared.files import delete_files

logger = logging.getLogger(__name__)

#: Stages in which the notes of a video may be edited (a summary must exist).
EDITABLE_STAGES = frozenset({Stage.PENDING_REVIEW, Stage.APPROVED, Stage.PUBLISHED})

ArtifactDeleter = Callable[[Iterable[str]], object]


async def _require_video(repo: PlaylistRepositoryPort, video_pk: int) -> Video:
    video = await repo.get_video(video_pk)
    if video is None:
        raise NotFoundError(f"Video {video_pk} not found")
    return video


class ApproveVideoUseCase:
    def __init__(self, repo: PlaylistRepositoryPort) -> None:
        self.repo = repo

    async def execute(self, video_pk: int) -> Video:
        video = await _require_video(self.repo, video_pk)
        assert_transition(video.stage, Stage.APPROVED)
        if not await self.repo.approve(video_pk):
            raise ConflictError(f"Video {video_pk} has no notes to approve yet")
        updated = await self.repo.get_video(video_pk)
        return updated or video


class ApproveAllUseCase:
    def __init__(self, repo: PlaylistRepositoryPort) -> None:
        self.repo = repo

    async def execute(self, playlist_pk: int) -> int:
        playlist = await self.repo.get_playlist(playlist_pk)
        if playlist is None:
            raise NotFoundError(f"Playlist {playlist_pk} not found")
        return await self.repo.approve_all_pending(playlist_pk)


class SaveSummaryEditUseCase:
    def __init__(self, repo: PlaylistRepositoryPort) -> None:
        self.repo = repo

    async def execute(self, video_pk: int, content: str) -> Summary:
        video = await _require_video(self.repo, video_pk)
        existing_summary = await self.repo.get_summary(video_pk)
        if existing_summary is None or video.stage not in EDITABLE_STAGES:
            raise ConflictError(
                f"Video {video_pk} cannot be edited in stage '{video.stage.value}': notes can "
                "only be edited once generated (pending review, approved or published)."
            )

        summary = await self.repo.upsert_summary(
            video_pk,
            content=content,
            provider=existing_summary.provider,
            model=existing_summary.model,
            prompt_version=existing_summary.prompt_version,
        )
        await self.repo.mark_edited(video_pk)
        updated = await self.repo.get_summary(video_pk)
        return updated or summary


class RetryVideoUseCase:
    def __init__(self, repo: PlaylistRepositoryPort) -> None:
        self.repo = repo

    async def execute(self, video_pk: int) -> Video:
        video = await _require_video(self.repo, video_pk)
        assert_transition(video.stage, Stage.QUEUED, is_retry=True)
        await self.repo.clear_failure(video_pk)
        await self.repo.update_stage(video_pk, Stage.QUEUED)
        updated = await self.repo.get_video(video_pk)
        return updated or video


class RegenerateVideoUseCase:
    """Queue a regeneration of a video's notes (PRD section 15, 16).

    This returns immediately: the video is re-queued with a regenerate flag and the
    background worker does the slow part. It reuses the stored transcript, or
    re-fetches it if it was cleaned up (PRD FR11). The previous summary stays in place
    until the new one arrives, then it is overwritten and approval is cleared.
    """

    def __init__(self, repo: PlaylistRepositoryPort) -> None:
        self.repo = repo

    async def execute(self, video_pk: int) -> Video:
        video = await _require_video(self.repo, video_pk)
        if not can_regenerate(video.stage):
            raise ConflictError(
                f"Video {video_pk} cannot be regenerated while in stage '{video.stage.value}'."
            )
        await self.repo.request_regenerate(video_pk)
        logger.info(
            "Regenerate queued",
            extra={"playlist_id": video.playlist_id, "video_id": video_pk},
        )
        updated = await self.repo.get_video(video_pk)
        return updated or video


class ReprocessPlaylistUseCase:
    def __init__(
        self,
        repo: PlaylistRepositoryPort,
        delete_artifacts: ArtifactDeleter = delete_files,
    ) -> None:
        self.repo = repo
        self.delete_artifacts = delete_artifacts

    async def execute(self, playlist_pk: int) -> bool:
        playlist = await self.repo.get_playlist(playlist_pk)
        if playlist is None:
            return False
        paths = await self.repo.get_transcript_artifact_paths(playlist_pk=playlist_pk)
        await self.repo.reset_playlist(playlist_pk)
        self.delete_artifacts(paths)
        return True


class DeletePlaylistUseCase:
    def __init__(
        self,
        repo: PlaylistRepositoryPort,
        delete_artifacts: ArtifactDeleter = delete_files,
    ) -> None:
        self.repo = repo
        self.delete_artifacts = delete_artifacts

    async def execute(self, playlist_pk: int) -> bool:
        playlist = await self.repo.get_playlist(playlist_pk)
        if playlist is None:
            return False
        paths = await self.repo.get_transcript_artifact_paths(playlist_pk=playlist_pk)
        deleted = await self.repo.delete_playlist(playlist_pk)
        if deleted:
            self.delete_artifacts(paths)
        return deleted


class CleanupTranscriptsUseCase:
    """Manual transcript cleanup (PRD FR11): clear the text and delete the raw subtitle files.

    The transcript row stays as an audit trail (``deleted_at``, size, hash); only its
    content is emptied and its artifact files removed. Notes are never touched.
    """

    def __init__(
        self,
        repo: PlaylistRepositoryPort,
        delete_artifacts: ArtifactDeleter = delete_files,
    ) -> None:
        self.repo = repo
        self.delete_artifacts = delete_artifacts

    async def execute_video(self, video_pk: int) -> bool:
        paths = await self.repo.get_transcript_artifact_paths(video_pk=video_pk)
        self.delete_artifacts(paths)
        return await self.repo.soft_delete_transcript(video_pk)

    async def execute_playlist(self, playlist_pk: int) -> int:
        paths = await self.repo.get_transcript_artifact_paths(playlist_pk=playlist_pk)
        self.delete_artifacts(paths)
        return await self.repo.soft_delete_all_transcripts(playlist_pk)
