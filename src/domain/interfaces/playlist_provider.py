"""Port for reading a YouTube playlist (PRD section 11, 21: integrations sit behind interfaces)."""

from __future__ import annotations

from typing import Protocol

from src.domain.services.sync_diff import PlaylistEntry


class PlaylistProviderPort(Protocol):
    """Fetches a playlist's title and its videos.

    Both methods are blocking (the Google client is synchronous); callers run
    them through ``asyncio.to_thread``.
    """

    def get_playlist_title(self, playlist_id: str) -> str:
        """Return the playlist title (``"Untitled Playlist"`` when it has none)."""
        ...

    def fetch_playlist_videos(self, playlist_id: str) -> list[PlaylistEntry]:
        """Return the playlist's videos in upstream order.

        ``position`` is **1-based**. Private and deleted videos are skipped.
        """
        ...
