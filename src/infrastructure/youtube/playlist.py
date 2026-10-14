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
from src.shared.api_keys import LONG_COOLDOWN_SECONDS, KeyPool, pool_for, read_api_keys
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
_KEY_REJECTED_REASONS = frozenset(
    {"keyInvalid", "keyExpired", "accessNotConfigured", "ipRefererBlocked"}
)
#: The live API answers a bad key with HTTP 400 reason ``badRequest`` (not ``keyInvalid``),
#: so the message is what identifies it.
_KEY_REJECTED_MESSAGES = ("api key not valid", "api key expired")
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
        explicit = (api_key or "").strip()
        self._api_key_env = api_key_env
        #: A pool of one for an explicit key; otherwise the shared pool for ``api_key_env``,
        #: re-read on every call so a key added to ``.env`` is picked up without a restart.
        self._own_pool = KeyPool([explicit]) if explicit else None
        keys = (explicit,) if explicit else read_api_keys(api_key_env)
        #: The first configured key (kept for callers that only know about a single key).
        self.api_key = keys[0] if keys else ""
        if not keys and service_factory is None:
            raise ConfigurationError(
                f"{api_key_env} is not set. Create a YouTube Data API v3 key in Google Cloud "
                f"and put it in .env as {api_key_env}=<key> (it is required to list playlists). "
                "Several keys can be given, separated by commas, to spread the quota."
            )
        self._service_factory = service_factory

    def _pool(self) -> KeyPool:
        return self._own_pool or pool_for(self._api_key_env)

    def _secrets(self) -> tuple[str, ...]:
        return self._pool().keys or (self.api_key,)

    def _service(self, key: str | None = None) -> Any:
        """A new service per call: ``httplib2`` objects must not be shared across threads."""
        if self._service_factory is not None:
            return self._service_factory()
        return build("youtube", "v3", developerKey=key or self.api_key, cache_discovery=False)

    def _key_problem(self, exc: Exception) -> str | None:
        """``"rejected"`` / ``"quota"`` when ``exc`` is about the key itself, else None."""
        if not isinstance(exc, HttpError):
            return None
        reasons = _error_reasons(exc)
        detail = redact_text(str(exc), *self._secrets()).lower()
        if reasons & _KEY_REJECTED_REASONS or any(m in detail for m in _KEY_REJECTED_MESSAGES):
            return "rejected"
        if reasons & _QUOTA_REASONS:
            return "quota"
        return None

    def _execute(self, what: str, call: Callable[[Any], Any]) -> Any:
        """Run ``call(service)``, moving to the next key when one is out of quota or rejected.

        Non-key failures propagate untouched for the caller to translate. When every key has
        failed for key reasons, the last failure is raised already translated.
        """
        if self._service_factory is not None:
            return call(self._service_factory())
        pool = self._pool()
        tried: set[str] = set()
        last: Exception | None = None
        while True:
            key = pool.acquire(exclude=tried)
            if key is None:
                if last is not None:
                    raise last
                raise PipelineError(
                    FailureType.RATE_LIMITED,
                    "Every configured YouTube API key is out of quota right now; try again later.",
                )
            tried.add(key)
            try:
                return call(self._service(key))
            except Exception as exc:
                problem = self._key_problem(exc)
                if problem is None:
                    raise
                if problem == "rejected":
                    pool.disable(key)
                else:
                    pool.cool_down(key, LONG_COOLDOWN_SECONDS)
                logger.warning(
                    "YouTube %s: a key was %s; %d of %d key(s) left to try",
                    what,
                    "rejected" if problem == "rejected" else "out of quota",
                    len(pool) - len(tried),
                    len(pool),
                )
                last = self._translate(exc, what)

    def _translate(self, exc: Exception, what: str) -> Exception:
        """Map a client exception to a classified, redacted Atlas error."""
        detail = redact_text(str(exc), *self._secrets())
        if isinstance(exc, HttpError):
            status = int(getattr(exc.resp, "status", 0) or 0)
            reasons = _error_reasons(exc)
            if reasons & _KEY_REJECTED_REASONS or any(
                message in detail.lower() for message in _KEY_REJECTED_MESSAGES
            ):
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
            # str(HttpError) embeds the request URL; the API's own reason is what a user
            # can act on, and it is what reaches the HTTP error body.
            reason = redact_text(str(getattr(exc, "reason", "") or ""), *self._secrets()).strip()
            return ProviderError(
                f"YouTube {what} failed (HTTP {status}): {reason or 'unexpected error'}",
                provider="youtube",
            )
        first_line = detail.strip().splitlines()[0] if detail.strip() else type(exc).__name__
        return ProviderError(f"YouTube {what} failed: {first_line[:200]}", provider="youtube")

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
            while True:
                response = self._execute(
                    "playlist fetch",
                    lambda service, token=page_token: service.playlistItems()
                    .list(
                        part="snippet,contentDetails",
                        playlistId=playlist_id,
                        maxResults=PAGE_SIZE,
                        pageToken=token,
                    )
                    .execute(),
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
            response = self._execute(
                "playlist title lookup",
                lambda service: service.playlists().list(part="snippet", id=playlist_id).execute(),
            )
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
