from __future__ import annotations

import logging
import os
import re
import socket
from datetime import datetime
from typing import Any

from googleapiclient.discovery import build

from src.domain.exceptions import ProviderError
from src.domain.interfaces.usage_ledger import UsageLedgerPort, UsageRecord
from src.domain.models.video import VideoId, VideoMetadata
from src.utils import get_config

logger = logging.getLogger(__name__)


def _parse_duration(iso_duration: str) -> str:
    """Parse ISO 8601 duration format (PT4M13S) to human-readable format (4:13)."""
    if not iso_duration:
        return "Unknown"

    pattern = r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?"
    match = re.match(pattern, iso_duration)

    if not match:
        return "Unknown"

    hours, minutes, seconds = match.groups()
    hours = int(hours) if hours else 0
    minutes = int(minutes) if minutes else 0
    seconds = int(seconds) if seconds else 0

    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    else:
        return f"{minutes}:{seconds:02d}"


class YouTubeDataApiSearchProvider:
    """YouTube Data API search provider.

    Preserves exact search logic from legacy youtube_video_search.py.
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
                "YouTube API key is required. Set YOUTUBE_API_KEY environment variable.",
                provider="youtube",
            )

        youtube_config = get_config("api.youtube", {})
        self.api_version = youtube_config.get("api_version", "v3")
        self.timeout = youtube_config.get("timeout", 30)
        self.default_type = youtube_config.get("type", "video")
        self.default_order = youtube_config.get("order", "relevance")

        self._youtube = build("youtube", self.api_version, developerKey=self.api_key)

    def search_videos(
        self, query: str, max_results: int | None = None
    ) -> list[VideoMetadata]:
        """Search for YouTube videos using the YouTube Data API.

        Args:
            query: The search query to find relevant YouTube videos
            max_results: Maximum number of results to return

        Returns:
            List of VideoMetadata objects

        Raises:
            ProviderError: If the API call fails
        """
        if max_results is None:
            max_results = get_config("search.default_max_results", 10)

        description_max_length = get_config("search.description_max_length", 200)

        try:
            socket.setdefaulttimeout(self.timeout)

            search_request = self._youtube.search().list(
                q=query,
                part="id,snippet",
                maxResults=max_results,
                type=self.default_type,
                order=self.default_order,
            )
            search_response = search_request.execute()

            video_ids: list[str] = []
            videos_data: list[dict[str, Any]] = []

            for search_result in search_response.get("items", []):
                if "id" not in search_result:
                    continue

                if (
                    isinstance(search_result["id"], dict)
                    and "videoId" in search_result["id"]
                ):
                    video_id = search_result["id"]["videoId"]
                elif isinstance(search_result["id"], str):
                    video_id = search_result["id"]
                else:
                    continue

                video_ids.append(video_id)
                videos_data.append(search_result)

            video_details_map: dict[str, dict[str, Any]] = {}
            if video_ids:
                video_details_request = self._youtube.videos().list(
                    part="contentDetails,statistics", id=",".join(video_ids)
                )
                video_details_response = video_details_request.execute()

                for video_detail in video_details_response.get("items", []):
                    video_details_map[video_detail["id"]] = video_detail

            videos: list[VideoMetadata] = []
            for i, search_result in enumerate(videos_data):
                video_id = video_ids[i]
                snippet = search_result["snippet"]

                description = snippet.get("description", "")
                if len(description) > description_max_length:
                    description = description[:description_max_length] + "..."

                duration = "Unknown"
                if video_id in video_details_map:
                    duration_iso = video_details_map[video_id]["contentDetails"]["duration"]
                    duration = _parse_duration(duration_iso)

                video_metadata = VideoMetadata(
                    video_id=VideoId(value=video_id),
                    title=snippet.get("title", ""),
                    channel=snippet.get("channelTitle", ""),
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    description=description,
                    published_at=snippet.get("publishedAt", ""),
                    duration=duration,
                )
                videos.append(video_metadata)

            if self.usage_ledger:
                record = UsageRecord(
                    timestamp=datetime.utcnow(),
                    provider="youtube",
                    operation="search",
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

            return videos

        except Exception as e:
            logger.exception("YouTube search failed")
            raise ProviderError(
                f"Error searching YouTube videos: {str(e)}", provider="youtube"
            ) from e
