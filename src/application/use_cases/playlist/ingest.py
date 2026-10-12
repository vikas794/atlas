"""Ingest or sync a YouTube playlist (PRD section 6 FR1, FR2, FR9, section 9).

Validates the playlist URL, checks if the playlist is already registered, and either
creates a new playlist record with initial videos or performs an incremental sync:
- Existing videos retain their position, title, and pipeline stage.
- New videos are appended continuing position from max(position) + 1 (positions are 1-based).
- A video that repeats inside the playlist gets its own row/position but is processed once.
- Missing upstream videos are marked with `removed_upstream = 1` (never deleted); a video that
  reappears has the flag cleared so the worker processes it again.
- Private or unavailable playlists raise `PipelineError(PLAYLIST_UNAVAILABLE)`.
- Submitting a known URL (or losing a concurrent-create race) syncs instead of failing.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from src.domain.exceptions import ConfigurationError, ProviderError
from src.domain.interfaces.playlist_provider import PlaylistProviderPort
from src.domain.interfaces.playlist_repository import PlaylistRepositoryPort
from src.domain.interfaces.usage_ledger import UsageLedgerPort, UsageRecord
from src.domain.models.playlist import FailureType, PipelineError, Playlist
from src.domain.services.playlist_url import parse_playlist_url
from src.domain.services.sync_diff import PlaylistEntry, diff_playlist
from src.shared.redaction import redact_text

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IngestResult:
    """Outcome of an ingest: the playlist and whether this call created it."""

    playlist: Playlist
    created: bool


class IngestPlaylistUseCase:
    """Use case to ingest or incrementally re-sync a YouTube playlist."""

    def __init__(
        self,
        repo: PlaylistRepositoryPort,
        youtube_provider: PlaylistProviderPort,
        usage_ledger: UsageLedgerPort | None = None,
    ) -> None:
        self.repo = repo
        self.youtube_provider = youtube_provider
        self.usage_ledger = usage_ledger
        # One ingest per playlist at a time: two concurrent submissions of the same URL
        # would otherwise race on positions (and on the UNIQUE playlist_id).
        self._locks: dict[str, asyncio.Lock] = {}

    async def execute(self, url: str) -> Playlist:
        """Ingest a new playlist or incrementally sync an existing one.

        Args:
            url: YouTube playlist URL (or watch URL with &list=).

        Returns:
            The created or updated Playlist entity.

        Raises:
            PipelineError: With FailureType.INVALID_URL or PLAYLIST_UNAVAILABLE.
        """
        return (await self.ingest(url)).playlist

    async def ingest(self, url: str) -> IngestResult:
        """Like :meth:`execute` but also reports whether the playlist was newly created."""
        playlist_id = parse_playlist_url(url)
        lock = self._locks.setdefault(playlist_id, asyncio.Lock())
        async with lock:
            existing = await self.repo.get_playlist_by_youtube_id(playlist_id)
            if existing is None:
                try:
                    return IngestResult(await self._create_new(playlist_id, url), created=True)
                except sqlite3.IntegrityError:
                    # Another process created it between our check and insert: sync it.
                    existing = await self.repo.get_playlist_by_youtube_id(playlist_id)
                    if existing is None:
                        raise
            return IngestResult(await self._sync_existing(existing), created=False)

    async def _fetch(self, playlist_id: str, *, with_title: bool) -> tuple[str, list[PlaylistEntry]]:
        """Fetch title and videos in worker threads; classify and redact failures."""
        try:
            title = ""
            if with_title:
                title = await asyncio.to_thread(self.youtube_provider.get_playlist_title, playlist_id)
            entries = await asyncio.to_thread(
                self.youtube_provider.fetch_playlist_videos, playlist_id
            )
        except (PipelineError, ConfigurationError, ProviderError):
            raise
        except Exception as exc:
            reason = redact_text(str(exc))
            logger.warning("Failed to fetch playlist %s: %s", playlist_id, reason)
            raise PipelineError(
                failure_type=FailureType.PLAYLIST_UNAVAILABLE,
                reason=f"YouTube playlist '{playlist_id}' is unavailable or private: {reason}",
            ) from None
        await self._record_usage()
        return title, list(entries)

    async def _record_usage(self) -> None:
        """Record the (free) API call from async context; never fails the ingest."""
        if self.usage_ledger is None:
            return
        try:
            await self.usage_ledger.record_usage(
                UsageRecord(
                    timestamp=datetime.now(UTC),
                    provider="youtube",
                    operation="playlist_fetch",
                    model="youtube-data-api-v3",
                    input_tokens=0,
                    output_tokens=0,
                    total_tokens=0,
                    cost_usd=0.0,
                    cache_hit=False,
                )
            )
        except Exception:
            logger.warning("Usage ledger write failed for playlist_fetch", exc_info=False)

    async def _create_new(self, playlist_id: str, source_url: str) -> Playlist:
        logger.info("Ingesting new playlist", extra={"playlist_id": playlist_id})
        title, entries = await self._fetch(playlist_id, with_title=True)

        if not entries and (not title or title == "Untitled Playlist"):
            raise PipelineError(
                failure_type=FailureType.PLAYLIST_UNAVAILABLE,
                reason=f"YouTube playlist '{playlist_id}' returned no videos or is unavailable.",
            )

        playlist = await self.repo.create_playlist(
            playlist_id=playlist_id,
            source_url=source_url,
            title=title or "Untitled Playlist",
        )

        # Positions are assigned here, dense and 1-based, in upstream order.
        diff = diff_playlist(entries, {})
        if diff.added:
            await self.repo.insert_videos(playlist.id, diff.added)
            await self.repo.update_playlist(playlist.id, video_count=len(diff.added))

        await self.repo.touch_synced(playlist.id)
        updated = await self.repo.get_playlist(playlist.id)
        return updated or playlist

    async def _sync_existing(self, existing: Playlist) -> Playlist:
        playlist_id = existing.playlist_id
        logger.info("Syncing existing playlist", extra={"playlist_id": existing.id})
        _title, incoming = await self._fetch(playlist_id, with_title=False)

        stored_videos = await self.repo.list_videos(existing.id, limit=None)
        first_positions: dict[str, int] = {}
        counts: dict[str, int] = {}
        for video in stored_videos:
            counts[video.youtube_video_id] = counts.get(video.youtube_video_id, 0) + 1
            first_positions.setdefault(video.youtube_video_id, video.position)

        diff = diff_playlist(
            incoming,
            first_positions,
            stored_counts=counts,
            max_position=max((v.position for v in stored_videos), default=0),
        )

        if diff.added:
            logger.info(
                "Appending %d new videos", len(diff.added), extra={"playlist_id": existing.id}
            )
            await self.repo.insert_videos(existing.id, diff.added)

        if diff.removed_youtube_ids:
            logger.info(
                "Flagging %d removed upstream videos",
                len(diff.removed_youtube_ids),
                extra={"playlist_id": existing.id},
            )
            removed = set(diff.removed_youtube_ids)
            pks = [v.id for v in stored_videos if v.youtube_video_id in removed]
            await self.repo.flag_removed_upstream(pks)

        returned = [
            v.id for v in stored_videos if v.removed_upstream and v.youtube_video_id in diff.reappeared_ids
        ]
        if returned:
            logger.info(
                "%d videos reappeared upstream; clearing the removed flag",
                len(returned),
                extra={"playlist_id": existing.id},
            )
            await self.repo.clear_removed_upstream(returned)

        await self.repo.update_playlist(
            existing.id, video_count=len(stored_videos) + len(diff.added)
        )
        await self.repo.touch_synced(existing.id)
        updated = await self.repo.get_playlist(existing.id)
        return updated or existing
