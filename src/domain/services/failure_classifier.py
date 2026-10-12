"""Map raw provider errors onto the Atlas failure taxonomy (PRD section 8).

Every failure ends up as a :class:`~src.domain.models.playlist.FailureType`,
which carries its own transient/permanent :class:`FailureKind`. Two rules matter
for the UI (design.md 6.2):

* the dashboard shows :func:`human_reason` verbatim, so reasons are written as
  short sentences and never leak the raw provider string;
* :func:`safe_diagnostic` is the only place raw provider text may reach a log,
  and only its first line, truncated.

Transcript text is never passed to, stored by, or logged by this module.
"""

from __future__ import annotations

import re
from typing import Final, NamedTuple

from src.domain.models.playlist import FailureKind, FailureType, kind_of


def _any_of(*phrases: str) -> re.Pattern[str]:
    """Compile phrases into one case-insensitive, word-boundary-anchored regex.

    Spaces in a phrase match any run of whitespace. Anchoring on word boundaries stops loose
    substring hits -- a video id such as ``a429bc`` is not a rate limit, ``Dnsmith`` is not a
    DNS failure and a title containing "deleted" is not a deleted video.
    """
    parts = [r"\s+".join(re.escape(word) for word in phrase.split()) for phrase in phrases]
    return re.compile(r"(?<![\w])(?:" + "|".join(parts) + r")(?![\w])", re.IGNORECASE)


#: Rate limiting is detected from several unrelated HTTP shapes, so the pattern is shared with
#: :func:`classify_output_error`. A bare "429" is not enough: it must follow "http error",
#: "status code" and the like, so ids and byte counts that contain 429 never match.
_RATE_LIMIT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:\b(?:http\s+error|status(?:\s+code)?|error(?:\s+code)?|code)\s*[:=]?\s*429\b)"
    r"|(?<![\w])(?:too[\s_]+many[\s_]+requests|rate[\s-]?limit(?:ed|ing)?)(?![\w])",
    re.IGNORECASE,
)

#: Ordered ``(failure type, pattern)`` rules. First match wins. Rate limiting comes
#: first: when YouTube throttles an IP, yt-dlp reports "Video unavailable. This content
#: isn't available, try again later. The current session has been rate-limited by
#: YouTube", which the availability rules below would otherwise read as a permanently
#: deleted video (taken at face value, the message itself says "try again later").
#: The specific video-availability rules then come before the generic transport ones.
_RULES: Final[tuple[tuple[FailureType, re.Pattern[str]], ...]] = (
    (FailureType.RATE_LIMITED, _RATE_LIMIT_PATTERN),
    (FailureType.RATE_LIMITED, _any_of("try again later")),
    (
        FailureType.VIDEO_PRIVATE,
        _any_of(
            "this video is private",
            "video is private",
            "private video",
            "sign in if you've been granted access",
        ),
    ),
    (
        FailureType.VIDEO_DELETED,
        _any_of(
            "removed by the uploader",
            "video has been removed",
            "has been removed",
            "video unavailable",
            "no longer available",
            "deleted video",
            "deleted by the uploader",
            "video has been deleted",
            "video was deleted",
        ),
    ),
    (
        FailureType.AGE_RESTRICTED,
        _any_of(
            "age-restricted",
            "age restricted",
            "age restriction",
            "confirm your age",
            "inappropriate for some users",
        ),
    ),
    (
        FailureType.REGION_BLOCKED,
        _any_of(
            "not available in your country",
            "available in your country",
            "blocked it in your country",
            "not available in your region",
            "not available from your location",
            "geo restricted",
            "geo-restricted",
            "georestricted",
        ),
    ),
    (
        FailureType.MEMBERS_ONLY,
        _any_of(
            "members-only",
            "members only",
            "join this channel",
        ),
    ),
    (
        FailureType.TRANSCRIPT_TOO_LARGE,
        _any_of(
            "transcript is too large",
            "too large to process",
            "exceeds the maximum",
            "exceeds maximum",
            "too big",
        ),
    ),
    # The uploader switched subtitles OFF: a distinct, permanent reason ...
    (
        FailureType.TRANSCRIPTS_DISABLED,
        _any_of(
            "subtitles are disabled",
            "subtitles disabled",
            "disabled subtitles",
            "captions are disabled",
            "captions disabled",
            "transcripts are disabled",
            "transcripts disabled",
        ),
    ),
    # ... as opposed to there being no subtitles / no usable language at all.
    (
        FailureType.NO_USABLE_LANGUAGE,
        _any_of(
            "no usable language",
            "requested language is not available",
            "requested subtitles are not available",
            "language is not available",
            "no transcript available in",
            "no subtitles",
            "no captions",
            "subtitles unavailable",
            "subtitles are unavailable",
            "unsupported language",
        ),
    ),
    (
        FailureType.NETWORK_ERROR,
        _any_of(
            "temporary failure in name resolution",
            "name resolution",
            "getaddrinfo",
            "timed out",
            "time out",
            "timeout",
            "dns",
            "connection reset",
            "connection aborted",
            "connection refused",
            "connection error",
            "unable to connect",
            "failed to establish a connection",
            "remote end closed",
            "network is unreachable",
            "network error",
            "ssl",
            "connection",
        ),
    ),
)

#: Short, user-facing sentences shown verbatim in the status dashboard
#: (design.md 6.2). No enum names, no underscores, no raw provider text.
_REASONS: Final[dict[FailureType, str]] = {
    FailureType.VIDEO_PRIVATE: "This video is private.",
    FailureType.VIDEO_DELETED: "This video was deleted by the uploader.",
    FailureType.AGE_RESTRICTED: "This video is age-restricted.",
    FailureType.REGION_BLOCKED: "This video is not available in your region.",
    FailureType.MEMBERS_ONLY: "This video is only available to channel members.",
    FailureType.TRANSCRIPTS_DISABLED: "Transcripts are disabled by the uploader.",
    FailureType.NO_USABLE_LANGUAGE: "No transcript is available in a usable language.",
    FailureType.TRANSCRIPT_TOO_LARGE: "The transcript is too large to process.",
    FailureType.RATE_LIMITED: "YouTube is rate-limiting requests; retrying shortly.",
    FailureType.NETWORK_ERROR: "A network error interrupted the request.",
    FailureType.PROVIDER_ERROR: "The transcript provider returned an error.",
    FailureType.OUTPUT_MALFORMED: "The provider returned output that could not be read.",
    FailureType.CONFIG_ERROR: "Atlas is not configured correctly; see the failure details.",
    FailureType.INTERNAL_ERROR: "An unexpected internal error occurred; see the server log.",
    FailureType.PLAYLIST_UNAVAILABLE: "This playlist is unavailable.",
    FailureType.INVALID_URL: "That does not look like a valid YouTube playlist URL.",
    FailureType.DRIVE_ERROR: "Google Drive returned an error.",
}

_DEFAULT_REASON: Final[str] = "Something went wrong."


class FailureInfo(NamedTuple):
    """A classified failure: what happened, whether to retry, and why."""

    failure_type: FailureType
    kind: FailureKind
    reason: str


def _text(value: object) -> str:
    """Normalize an exception or string into lowercase text (the annotation is
    ``str`` for callers, but provider code often hands us an exception)."""
    return (value if isinstance(value, str) else str(value)).strip().lower()


def classify_transcript_error(detail: str) -> FailureType:
    """Classify a yt-dlp / network error into the PRD section 8 taxonomy.

    Anything unrecognized becomes :attr:`FailureType.PROVIDER_ERROR` (transient)
    rather than guessing a permanent cause — v1 treats errors at face value.
    """
    text = _text(detail)
    if not text:
        return FailureType.PROVIDER_ERROR
    for failure_type, pattern in _RULES:
        if pattern.search(text):
            return failure_type
    return FailureType.PROVIDER_ERROR


def classify_output_error(detail: str) -> FailureType:
    """Classify a malformed/undecodable model response.

    A rate-limit can surface here as text instead of JSON, so that case is
    classified as such; everything else is malformed output (transient).
    """
    text = _text(detail)
    if text and _RATE_LIMIT_PATTERN.search(text):
        return FailureType.RATE_LIMITED
    return FailureType.OUTPUT_MALFORMED


def classify_playlist_error(detail: str) -> FailureType:
    """Classify a playlist-level error; falls back to the per-video rules."""
    text = _text(detail)
    if any(pattern in text for pattern in ("invalid url", "not a valid youtube", "unsupported url")):
        return FailureType.INVALID_URL
    if any(
        pattern in text
        for pattern in ("playlist does not exist", "playlist not found", "playlist unavailable")
    ):
        return FailureType.PLAYLIST_UNAVAILABLE
    return classify_transcript_error(detail)


def classify_drive_error(detail: str) -> FailureType:
    """Classify a Google Drive error.

    Drive failures are not video-specific, so the context already decides the
    type; the detail is only kept for :func:`safe_diagnostic`.
    """
    return FailureType.DRIVE_ERROR


def classify_error(detail: str) -> FailureInfo:
    """Classify a transcript-provider error into the full triple."""
    failure_type = classify_transcript_error(detail)
    return FailureInfo(failure_type, kind_of(failure_type), human_reason(failure_type))


def human_reason(failure_type: FailureType) -> str:
    """Short sentence shown to the user; never contains raw provider text."""
    return _REASONS.get(FailureType(failure_type), _DEFAULT_REASON)


def safe_diagnostic(detail: str, limit: int = 200) -> str:
    """First line of a provider error, truncated, for logs only."""
    text = (detail if isinstance(detail, str) else str(detail)).strip()
    if not text:
        return ""
    first_line = " ".join(text.splitlines()[0].split())
    if len(first_line) <= limit:
        return first_line
    if limit <= 3:
        return first_line[:limit]
    return first_line[: limit - 3].rstrip() + "..."
