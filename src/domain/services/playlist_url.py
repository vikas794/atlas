"""Playlist URL parsing and validation service (PRD section 6, FR1).

Extracts the YouTube playlist ID from standard playlist URLs, watch URLs with
a `list=` query parameter, short URLs, and URLs with extra parameters.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from src.domain.models.playlist import FailureType, PipelineError


def parse_playlist_url(url: str) -> str:
    """Parse and extract the YouTube playlist ID from a URL string.

    Supported formats:
    - https://www.youtube.com/playlist?list=PL...
    - https://youtube.com/watch?v=...&list=PL...
    - https://youtu.be/...?list=PL...
    - youtube.com/playlist?list=PL...
    - URLs with extra query parameters (e.g., &index=1, &si=...)

    Args:
        url: Raw URL string or playlist reference.

    Returns:
        The extracted playlist ID string.

    Raises:
        PipelineError: With FailureType.INVALID_URL if the URL cannot be parsed
            or does not contain a valid playlist ID.
    """
    if not url or not isinstance(url, str):
        raise PipelineError(
            failure_type=FailureType.INVALID_URL,
            reason="Playlist URL cannot be empty.",
        )

    cleaned = url.strip()
    if not cleaned:
        raise PipelineError(
            failure_type=FailureType.INVALID_URL,
            reason="Playlist URL cannot be empty.",
        )

    # Allow passing directly without scheme
    if not cleaned.startswith(("http://", "https://")):
        cleaned = f"https://{cleaned}"

    try:
        parsed = urlparse(cleaned)
    except Exception as exc:
        raise PipelineError(
            failure_type=FailureType.INVALID_URL,
            reason=f"Failed to parse URL: {exc}",
        ) from exc

    hostname = (parsed.hostname or "").lower()

    # Also check suffix (e.g. subdomains)
    is_youtube = any(
        hostname == v or hostname.endswith(f".{v}") for v in ("youtube.com", "youtu.be")
    )
    if not is_youtube:
        raise PipelineError(
            failure_type=FailureType.INVALID_URL,
            reason=f"URL host '{parsed.netloc}' is not a recognized YouTube domain.",
        )

    query = parse_qs(parsed.query)
    list_params = query.get("list")

    if not list_params or not list_params[0].strip():
        raise PipelineError(
            failure_type=FailureType.INVALID_URL,
            reason="URL does not contain a 'list' playlist parameter.",
        )

    playlist_id = list_params[0].strip()
    return playlist_id
