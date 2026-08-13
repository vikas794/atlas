from __future__ import annotations

import logging
import os
from datetime import datetime

from googleapiclient.discovery import build

from src.domain.exceptions import ProviderError
from src.domain.interfaces.usage_ledger import UsageLedgerPort, UsageRecord
from src.domain.models.video import VideoId, VideoMetadata

logger = logging.getLogger(__name__)


class YouTubePlaylistProvider:
    """YouTube playlist video fetcher.

    Preserves exact logic from legacy playlist_quiz_generator.py:PlaylistVideoFetcher.
    """

    def __init__(
        self,
        usage_ledger: UsageLedgerPort | None = None,
        api_key: str | None = None,
    ) -> None:
        self.usage_ledger = usage_ledger
        self.api_key = api_key or os.getenv("YOUTUBE_API_KEY")
        if not self.api_key:
            raise ProviderError(
                "YOUTUBE_API_KEY is not set.", provider="youtube"
            )
        self._youtube = build("youtube", "v3", developerKey=self.api_key)
        self.logger = logging.getLogger(__name__)

    def fetch_playlist_videos(
        self, playlist_id: str, max_videos: int | None = None
    ) -> list[VideoMetadata]:
        """Fetches videos from a playlist.

        Returns list of VideoMetadata with video_id, title, channel, position.

        Args:
            playlist_id: YouTube playlist ID
            max_videos: Maximum number of videos to fetch (None for all)

        Returns:
            List of VideoMetadata objects

        Raises:
            ProviderError: If the API call fails
        """
        videos: list[VideoMetadata] = []
        next_page_token: str | None = None

        try:
            while True:
                request = self._youtube.playlistItems().list(
                    part="snippet,contentDetails",
                    playlistId=playlist_id,
                    maxResults=50,
                    pageToken=next_page_token,
                )
                response = request.execute()

                for item in response.get("items", []):
                    snippet = item.get("snippet", {})
                    video_title = snippet.get("title", "")
                    if video_title in ["Private video", "Deleted video"]:
                        continue

                    video_id = snippet.get("resourceId", {}).get("videoId")
                    if video_id:
                        videos.append(
                            VideoMetadata(
                                video_id=VideoId(value=video_id),
                                title=video_title,
                                position=snippet.get("position", 0),
                                channel=snippet.get("videoOwnerChannelTitle", ""),
                                url=f"https://www.youtube.com/watch?v={video_id}",
                                description="",
                                published_at=snippet.get("publishedAt", ""),
                                duration="Unknown",
                            )
                        )

                    if max_videos and len(videos) >= max_videos:
                        break

                next_page_token = response.get("nextPageToken")
                if not next_page_token or (
                    max_videos and len(videos) >= max_videos
                ):
                    break

            result = videos[:max_videos] if max_videos else videos

            if self.usage_ledger:
                record = UsageRecord(
                    timestamp=datetime.utcnow(),
                    provider="youtube",
                    operation="playlist_fetch",
                    model="youtube-data-api-v3",
                    input_tokens=0,
                    output_tokens=0,
                    total_tokens=0,
                    cost_usd=0.0,
                    cache_hit=False,
                    run_id=None,
                    video_id=None,
                )
                import asyncio
                asyncio.create_task(self.usage_ledger.record_usage(record))

            return result

        except Exception as e:
            self.logger.exception("YouTube playlist fetch failed")
            raise ProviderError(
                f"Error fetching playlist videos: {str(e)}", provider="youtube"
            ) from e

    def get_playlist_title(self, playlist_id: str) -> str:
        """Get the title of a playlist.

        Args:
            playlist_id: YouTube playlist ID

        Returns:
            Playlist title or "Untitled Playlist" if not found
        """
        try:
            request = self._youtube.playlists().list(
                part="snippet",
                id=playlist_id,
            )
            response = request.execute()
            items = response.get("items", [])
            if items:
                return items[0]["snippet"]["title"]
            return "Untitled Playlist"
        except Exception as e:
            self.logger.exception("Failed to get playlist title")
            raise ProviderError(
                f"Error getting playlist title: {str(e)}", provider="youtube"
            ) from e
