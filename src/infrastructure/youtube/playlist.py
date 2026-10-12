"""YouTube Data API adapter that lists a playlist's videos (PRD section 5, stage 1).

Three properties matter here:

* **1-based positions.** The API's ``snippet.position`` is 0-based; Atlas numbers
  Docs from ``001`` (PRD section 19), so the provider hands out dense 1-based
  positions over the videos it keeps. Private and deleted placeholders are
  skipped, so the numbering has no holes.
* **Thread safety.** The methods are blocking and run in ``asyncio.to_thread``.
  ``httplib2`` (under ``googleapiclient``) is not thread-safe, so a fresh service
  is built per call rather than shared. Nothing here touches the event loop:
  usage recording is the caller's job (it runs in async context).
* **No key leaks.** ``HttpError`` text contains the request URL including
  ``key=<API key>``. Every message that leaves this module is redacted, and
  tracebacks are never logged (they would print the same text).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.domain.exceptions import ConfigurationError, ProviderError
from src.domain.models.playlist import FailureType, PipelineError
from src.domain.services.sync_diff import PlaylistEntry
from src.shared.redaction import redact_text

logger = logging.getLogger(__name__)

#: Titles the API returns for items it cannot describe.
_PLACEHOLDER_TITLES = frozenset({"Private video", "Deleted video"})

#: The playlistItems endpoint caps a page at 50 items.
PAGE_SIZE = 50

DEFAULT_API_KEY_ENV = "YOUTUBE_API_KEY"

#: ``error.errors[].reason`` values that mean "this playlist cannot be read".
_PLAYLIST_UNAVAILABLE_REASONS = frozenset(
    # "invalid" is what the API answers (HTTP 400) for a malformed playlist id.
    {"playlistNotFound", "playlistItemsNotAccessible", "playlistForbidden", "forbidden", "invalid"}
)
#: Reasons that mean Atlas' own key is wrong or not enabled for the Data API.
_KEY_REJECTED_REASONS = frozenset({"keyInvalid", "accessNotConfigured", "ipRefererBlocked"})
_QUOTA_REASONS = frozenset(
    {"quotaExceeded", "rateLimitExceeded", "userRateLimitExceeded", "dailyLimitExceeded"}
)


def _error_reasons(exc: HttpError) -> set[str]:
    """Extract ``error.errors[].reason`` codes from an HttpError body, best effort."""
    try:
        body = json.loads(exc.content.decode("utf-8")) if exc.content else {}
    except (ValueError, AttributeError):
        return set()
    errors = body.get("error", {}).get("errors", []) if isinstance(body, dict) else []
    return {str(item.get("reason", "")) for item in errors if isinstance(item, dict)}


class YouTubePlaylistProvider:
    """Reads playlist titles and videos from the YouTube Data API v3.

    Args:
        api_key: The API key. When omitted it is read from ``api_key_env``.
        api_key_env: Name of the environment variable that holds the key (used
            for the error message only when ``api_key`` is not given).
        service_factory: Builds a ``googleapiclient`` YouTube service. Tests
            inject a stub here; production builds a real service per call.

    Raises:
        ConfigurationError: If no key is available and no ``service_factory`` is given.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        api_key_env: str = DEFAULT_API_KEY_ENV,
        service_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.api_key = (api_key or os.getenv(api_key_env) or "").strip()
        if not self.api_key and service_factory is None:
            raise ConfigurationError(
                f"{api_key_env} is not set. Create a YouTube Data API v3 key in Google Cloud "
                f"and put it in .env as {api_key_env}=<key> (it is required to list playlists)."
            )
        self._service_factory = service_factory

    def _service(self) -> Any:
        """A new service per call: ``httplib2`` objects must not be shared across threads."""
        if self._service_factory is not None:
            return self._service_factory()
        return build("youtube", "v3", developerKey=self.api_key, cache_discovery=False)

    def _translate(self, exc: Exception, what: str) -> Exception:
        """Map a client exception to a classified, redacted Atlas error."""
        detail = redact_text(str(exc), self.api_key)
        if isinstance(exc, HttpError):
            status = int(getattr(exc.resp, "status", 0) or 0)
            reasons = _error_reasons(exc)
            if reasons & _KEY_REJECTED_REASONS:
                return ConfigurationError(
                    "The YouTube API rejected the configured key (YOUTUBE_API_KEY). Check that it "
                    "is valid and that the YouTube Data API v3 is enabled for its project."
                )
            if reasons & _QUOTA_REASONS:
                return PipelineError(
                    FailureType.RATE_LIMITED,
                    "The YouTube Data API quota was exceeded; try again later.",
                )
            if status == 404 or reasons & _PLAYLIST_UNAVAILABLE_REASONS:
                return PipelineError(
                    FailureType.PLAYLIST_UNAVAILABLE,
                    "The playlist was not found or is private.",
                )
            return ProviderError(f"YouTube {what} failed (HTTP {status}): {detail}", provider="youtube")
        return ProviderError(f"YouTube {what} failed: {detail}", provider="youtube")

    def fetch_playlist_videos(
        self, playlist_id: str, max_videos: int | None = None
    ) -> list[PlaylistEntry]:
        """Fetch a playlist's videos in upstream order with dense **1-based** positions.

        Private and deleted placeholders are skipped (and not numbered). Pages of
        50 are followed until exhausted or ``max_videos`` is reached.

        Raises:
            PipelineError: ``PLAYLIST_UNAVAILABLE`` (missing/private) or
                ``RATE_LIMITED`` (quota).
            ConfigurationError: The API key is rejected.
            ProviderError: Any other API or network failure (message redacted).
        """
        entries: list[PlaylistEntry] = []
        page_token: str | None = None
        try:
            service = self._service()
            while True:
                response = (
                    service.playlistItems()
                    .list(
                        part="snippet,contentDetails",
                        playlistId=playlist_id,
                        maxResults=PAGE_SIZE,
                        pageToken=page_token,
                    )
                    .execute()
                )
                for item in response.get("items", []):
                    snippet = item.get("snippet", {})
                    title = snippet.get("title", "")
                    if title in _PLACEHOLDER_TITLES:
                        continue
                    video_id = snippet.get("resourceId", {}).get("videoId") or item.get(
                        "contentDetails", {}
                    ).get("videoId")
                    if not video_id:
                        continue
                    entries.append(
                        PlaylistEntry(
                            youtube_video_id=video_id,
                            title=title,
                            position=len(entries) + 1,
                        )
                    )
                    if max_videos and len(entries) >= max_videos:
                        return entries
                page_token = response.get("nextPageToken")
                if not page_token:
                    return entries
        except (PipelineError, ConfigurationError):
            raise
        except Exception as exc:
            translated = self._translate(exc, "playlist fetch")
            # Message only, never a traceback: HttpError repr carries the key URL.
            logger.warning("YouTube playlist fetch failed for %s: %s", playlist_id, translated)
            raise translated from None

    def get_playlist_title(self, playlist_id: str) -> str:
        """Return the playlist title, or ``"Untitled Playlist"`` when the API has none."""
        try:
            response = self._service().playlists().list(part="snippet", id=playlist_id).execute()
        except (PipelineError, ConfigurationError):
            raise
        except Exception as exc:
            translated = self._translate(exc, "playlist title lookup")
            logger.warning("YouTube title lookup failed for %s: %s", playlist_id, translated)
            raise translated from None
        items = response.get("items", [])
        if items:
            return str(items[0].get("snippet", {}).get("title") or "Untitled Playlist")
        return "Untitled Playlist"
