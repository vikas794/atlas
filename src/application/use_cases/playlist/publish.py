"""Use case for publishing approved video notes to Google Drive (PRD section 15, 16, 19).

Two entry points:

* :meth:`PublishPlaylistUseCase.request_publish` -- what the HTTP handler calls. It validates
  the request, checks Drive consent, and marks the playlist ``publishing``; it returns
  immediately and the background worker runs the job (PRD section 16: long work never
  blocks a request).
* :meth:`PublishPlaylistUseCase.run` -- what the worker calls. It runs :meth:`execute` and
  records ``published`` or ``publish_failed`` (+ a redacted reason) on the playlist.

Idempotency (PRD section 15, 19): the publisher reports each Drive object to a progress
sink the moment it exists, and the sink persists it immediately -- the folder id, the index
Doc id and every video Doc id (and its ``published`` mark). A publish that fails half-way is
resumed by running it again: persisted ids are reused and nothing is created twice. The
only unprotected window is the instant between Drive returning a new id and the database
write that stores it.

Doc naming: positions are 1-based; the zero-pad width is that of the playlist's largest
position and never shrinks (``playlists.pad_width``). When it grows (99 -> 100 videos), the
already-published Docs are renamed (number unchanged, padding only) so Drive's sort stays
correct; nothing is renamed while the width is unchanged.

Index Doc: a numbered list of the playlist's video titles in position order; each published
video's title links to its Doc and is followed by a short description taken from the notes'
Summary section (``extract_short_summary``). It is written after the video Docs, and rewritten
in place whenever its content changes (tracked by ``playlists.index_digest``); rewriting
instead of blindly appending keeps the order correct even when videos are approved out of
order, and makes a failed index update self-healing on the next publish.
"""

from __future__ import annotations

import logging

from src.domain.exceptions import ConflictError, NotFoundError
from src.domain.interfaces.drive_publisher import (
    DriveFolderMissing,
    DrivePublisherPort,
    IndexDetail,
    PublishEntry,
    PublishResult,
)
from src.domain.interfaces.playlist_repository import PlaylistRepositoryPort
from src.domain.models.playlist import Playlist, Stage, Summary, Video
from src.domain.services.doc_naming import pad_width as compute_pad_width
from src.domain.services.notes_summary import extract_short_summary
from src.shared.redaction import redact_text
from src.shared.text import sha256_text

logger = logging.getLogger(__name__)


class _PersistingProgress:
    """Persists every Drive object as soon as the publisher reports it."""

    def __init__(
        self,
        repo: PlaylistRepositoryPort,
        playlist_pk: int,
        qualifying: dict[int, Summary],
        index_digest: str,
    ) -> None:
        self.repo = repo
        self.playlist_pk = playlist_pk
        self.qualifying = qualifying
        self.index_digest = index_digest

    async def folder_created(self, folder_id: str) -> None:
        await self.repo.set_drive_folder(self.playlist_pk, folder_id)

    async def index_doc_created(self, doc_id: str) -> None:
        await self.repo.set_drive_index_doc(self.playlist_pk, doc_id)

    async def index_written(self) -> None:
        await self.repo.set_index_digest(self.playlist_pk, self.index_digest)

    async def doc_created(self, video_pk: int, doc_id: str) -> None:
        await self.repo.set_drive_doc_id(video_pk, doc_id)

    async def doc_published(self, video_pk: int, doc_id: str, *, created: bool) -> None:
        await self.repo.set_drive_doc_id(video_pk, doc_id)
        summary = self.qualifying.get(video_pk)
        if summary is not None:
            await self.repo.mark_published(video_pk, digest=sha256_text(summary.content))


class PublishPlaylistUseCase:
    """Publishes approved notes for a playlist to Google Drive."""

    def __init__(
        self,
        repo: PlaylistRepositoryPort,
        publisher: DrivePublisherPort,
    ) -> None:
        self.repo = repo
        self.publisher = publisher

    async def request_publish(self, playlist_pk: int) -> Playlist:
        """Validate a publish request and mark the playlist ``publishing``.

        Raises:
            NotFoundError: Unknown playlist.
            DriveAuthorizationRequired: Drive consent has not been completed.
            ConflictError: A publish is already running for this playlist.
        """
        playlist = await self.repo.get_playlist(playlist_pk)
        if playlist is None:
            raise NotFoundError(f"Playlist {playlist_pk} not found")
        await self.publisher.ensure_authorized()
        if not await self.repo.begin_publish(playlist_pk):
            raise ConflictError(f"Playlist {playlist_pk} is already being published")
        updated = await self.repo.get_playlist(playlist_pk)
        return updated or playlist

    async def run(self, playlist_pk: int) -> PublishResult | None:
        """Run a requested publish in the background and record its outcome.

        Never raises: a failure is stored as ``publish_failed`` with a redacted reason that
        the UI shows, and logged with its traceback.
        """
        ctx = {"playlist_id": playlist_pk}
        try:
            result = await self.execute(playlist_pk)
        except Exception as exc:
            reason = redact_text(getattr(exc, "reason", None) or str(exc) or type(exc).__name__)
            logger.exception("Publish failed", extra=ctx)
            await self.repo.finish_publish(playlist_pk, reason)
            return None
        await self.repo.finish_publish(playlist_pk, None)
        logger.info(
            "Publish finished: %d created, %d updated",
            result.created,
            result.updated,
            extra=ctx,
        )
        return result

    async def execute(self, playlist_pk: int) -> PublishResult:
        """Publish approved or edited-published videos to Drive in playlist order.

        If the playlist's Drive folder was deleted or trashed by the user, the stored
        folder, index and Doc ids are forgotten and the playlist is published again into a
        new folder (every already-published video is re-created from its published
        notes). Nothing unapproved is added by this: only videos already published or
        approved qualify, exactly as on a normal publish.
        """
        try:
            return await self._execute(playlist_pk)
        except DriveFolderMissing as exc:
            logger.warning(
                "Drive folder %s is %s; re-publishing the playlist into a new folder",
                exc.folder_id,
                exc.state,
                extra={"playlist_id": playlist_pk},
            )
            await self._forget_drive_ids(playlist_pk)
            return await self._execute(playlist_pk)

    async def _forget_drive_ids(self, playlist_pk: int) -> None:
        await self.repo.set_drive_folder(playlist_pk, None)
        await self.repo.set_drive_index_doc(playlist_pk, None)
        await self.repo.set_index_digest(playlist_pk, None)
        for video in await self.repo.list_videos(playlist_pk):
            if video.drive_doc_id:
                await self.repo.set_drive_doc_id(video.id, None)

    async def _execute(self, playlist_pk: int) -> PublishResult:
        playlist = await self.repo.get_playlist(playlist_pk)
        if playlist is None:
            raise NotFoundError(f"Playlist {playlist_pk} not found")

        videos = await self.repo.list_videos(playlist_pk)
        max_position = max((v.position for v in videos), default=0)
        # The width never shrinks, so already-published Doc names stay consistent.
        width = max(compute_pad_width(max_position), playlist.pad_width)

        entries: list[PublishEntry] = []
        qualifying: dict[int, Summary] = {}
        published_videos: list[Video] = []

        #: position -> short description, only for videos that have (or are getting) a Doc.
        descriptions: dict[int, str] = {}
        #: Positions whose index title links to a Doc once this publish is done.
        linked_positions: set[int] = set()

        for video in videos:
            if video.stage == Stage.APPROVED:
                summary = await self.repo.get_summary(video.id)
                if summary:
                    entries.append(self._entry(video, summary))
                    qualifying[video.id] = summary
                    linked_positions.add(video.position)
                    description = extract_short_summary(summary.content)
                    if description:
                        descriptions[video.position] = description
            elif video.stage == Stage.PUBLISHED:
                published_videos.append(video)
                summary = await self.repo.get_summary(video.id)
                linked_positions.add(video.position)
                description = extract_short_summary(summary.content) if summary else None
                if description:
                    descriptions[video.position] = description
                edited_since_publish = bool(
                    summary
                    and summary.edited
                    and sha256_text(summary.content) != summary.published_digest
                )
                # No stored Doc id: its folder was deleted and is being re-created.
                if summary and (edited_since_publish or not video.drive_doc_id):
                    entries.append(self._entry(video, summary))
                    qualifying[video.id] = summary

        # Width grew since the published Docs were named: rename them (padding only).
        if playlist.pad_width != width:
            in_entries = {e.video_pk for e in entries}
            for video in published_videos:
                if video.drive_doc_id and video.id not in in_entries:
                    summary = await self.repo.get_summary(video.id)
                    entries.append(
                        PublishEntry(
                            video_pk=video.id,
                            position=video.position,
                            title=video.title,
                            # Only used if the Doc vanished from Drive and is re-created.
                            markdown=summary.content if summary else "",
                            existing_doc_id=video.drive_doc_id,
                            rename_only=True,
                        )
                    )
            entries.sort(key=lambda e: e.position)

        index_titles = [(v.position, v.title) for v in videos]
        index_details = {
            v.position: IndexDetail(
                description=descriptions.get(v.position),
                doc_id=v.drive_doc_id if v.position in linked_positions else None,
            )
            for v in videos
            if v.position in descriptions or v.position in linked_positions
        }
        # Whether a title is linked (not the Doc id itself) is hashed: ids of Docs created by
        # this publish are not known yet, and the digest must match on the next run.
        index_digest = sha256_text(
            "\n".join(
                f"{position}\t{title}\t{descriptions.get(position, '')}"
                f"\t{int(position in linked_positions)}"
                for position, title in index_titles
            )
        )
        has_docs = bool(published_videos) or bool(entries)
        index_stale = has_docs and (
            playlist.drive_index_doc_id is None or index_digest != playlist.index_digest
        )

        if not entries and not (published_videos and index_stale):
            logger.info(
                "No approved or edited videos to publish", extra={"playlist_id": playlist_pk}
            )
            return PublishResult(
                folder_id=playlist.drive_folder_id or "",
                index_doc_id=playlist.drive_index_doc_id,
                created=0,
                updated=0,
                skipped=len(videos),
                doc_ids={},
            )

        progress = _PersistingProgress(self.repo, playlist.id, qualifying, index_digest)
        result = await self.publisher.publish_playlist(
            playlist_pk=playlist.id,
            playlist_title=playlist.title,
            pad_width=width,
            entries=entries,
            index_titles=index_titles,
            update_index=index_stale,
            existing_folder_id=playlist.drive_folder_id,
            existing_index_doc_id=playlist.drive_index_doc_id,
            progress=progress,
            index_details=index_details,
        )

        # Safety net for publishers that do not report progress: persist what they returned.
        if result.folder_id and result.folder_id != playlist.drive_folder_id:
            await self.repo.set_drive_folder(playlist.id, result.folder_id)
        if result.index_doc_id and result.index_doc_id != playlist.drive_index_doc_id:
            await self.repo.set_drive_index_doc(playlist.id, result.index_doc_id)
        if playlist.pad_width != width:
            await self.repo.set_pad_width(playlist.id, width)

        logger.info(
            "Published playlist: %d created, %d updated, %d skipped",
            result.created,
            result.updated,
            result.skipped,
            extra={"playlist_id": playlist_pk},
        )
        return result

    @staticmethod
    def _entry(video: Video, summary: Summary) -> PublishEntry:
        return PublishEntry(
            video_pk=video.id,
            position=video.position,
            title=video.title,
            markdown=summary.content,
            existing_doc_id=video.drive_doc_id,
        )
