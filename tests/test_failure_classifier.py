"""Failure taxonomy mapping (PRD section 8) and the dashboard reason strings."""

from __future__ import annotations

import pytest

from src.domain.models.playlist import FAILURE_KINDS, FailureKind, FailureType, kind_of
from src.domain.services.failure_classifier import (
    classify_drive_error,
    classify_error,
    classify_output_error,
    classify_playlist_error,
    classify_transcript_error,
    human_reason,
    safe_diagnostic,
)

#: (classifier, raw provider text, expected FailureType) — one realistic case per
#: FailureType in the PRD section 8 taxonomy, plus the unknown-error fallbacks.
CASES: list[tuple[object, str, FailureType]] = [
    (classify_transcript_error, "ERROR: [youtube] abc123: Private video",
     FailureType.VIDEO_PRIVATE),
    (classify_transcript_error, "This video is private. Sign in if you've been granted access",
     FailureType.VIDEO_PRIVATE),
    (classify_transcript_error,
     "ERROR: [youtube] abc123: Video unavailable. This video has been removed by the uploader",
     FailureType.VIDEO_DELETED),
    (classify_transcript_error, "This video has been deleted by the uploader",
     FailureType.VIDEO_DELETED),
    (classify_transcript_error,
     "ERROR: [youtube] abc123: Sign in to confirm your age. This video may be inappropriate",
     FailureType.AGE_RESTRICTED),
    (classify_transcript_error, "This video is age-restricted", FailureType.AGE_RESTRICTED),
    (classify_transcript_error,
     "The uploader has not made this video available in your country", FailureType.REGION_BLOCKED),
    (classify_transcript_error, "This video is not available in your region",
     FailureType.REGION_BLOCKED),
    (classify_transcript_error,
     "Join this channel to get access to members-only content like this video",
     FailureType.MEMBERS_ONLY),
    (classify_transcript_error,
     "ERROR: [youtube] abc123: This video is available to this channel's members only",
     FailureType.MEMBERS_ONLY),
    (classify_transcript_error, "Subtitles are disabled for this video",
     FailureType.TRANSCRIPTS_DISABLED),
    (classify_transcript_error, "WARNING: There are no subtitles for the requested languages",
     FailureType.NO_USABLE_LANGUAGE),
    (classify_transcript_error, "Requested subtitles are not available for this video",
     FailureType.NO_USABLE_LANGUAGE),
    (classify_transcript_error, "ERROR: Requested language is not available",
     FailureType.NO_USABLE_LANGUAGE),
    (classify_transcript_error, "The transcript is too large to process",
     FailureType.TRANSCRIPT_TOO_LARGE),
    (classify_transcript_error,
     "ERROR: unable to download API page: HTTP Error 429: Too Many Requests",
     FailureType.RATE_LIMITED),
    (classify_transcript_error, "You have been rate-limited by YouTube", FailureType.RATE_LIMITED),
    (classify_transcript_error, "ERROR: unable to download video data: <urlopen error timed out>",
     FailureType.NETWORK_ERROR),
    (classify_transcript_error, "HTTP Error 503: Service Unavailable (connection reset by peer)",
     FailureType.NETWORK_ERROR),
    (classify_transcript_error, "Temporary failure in name resolution", FailureType.NETWORK_ERROR),
    (classify_transcript_error, "ERROR: [generic] Extractor failed. Please report this issue.",
     FailureType.PROVIDER_ERROR),
    (classify_output_error, "Expecting value: line 1 column 1 (char 0)", FailureType.OUTPUT_MALFORMED),
    (classify_playlist_error, "Playlist does not exist", FailureType.PLAYLIST_UNAVAILABLE),
    (classify_playlist_error, "ERROR: Invalid URL for playlist", FailureType.INVALID_URL),
    (classify_drive_error, "403 insufficientPermissions on file", FailureType.DRIVE_ERROR),
]

#: Atlas-side failures are raised directly by the pipeline (never classified from provider
#: text), so they have no classifier case; they must still be permanent and have a reason.
PIPELINE_ONLY_TYPES = {FailureType.CONFIG_ERROR, FailureType.INTERNAL_ERROR}


def test_cases_cover_every_failure_type() -> None:
    covered = {expected for _fn, _detail, expected in CASES}
    assert covered | PIPELINE_ONLY_TYPES == set(FailureType)


def test_atlas_side_failures_are_permanent() -> None:
    for failure_type in PIPELINE_ONLY_TYPES:
        assert kind_of(failure_type) is FailureKind.PERMANENT


# --- M11: word-boundary matching, and "no subtitles" vs "disabled" ---------------------


@pytest.mark.parametrize(
    "detail",
    [
        "ERROR: [youtube] a429bc: something odd happened",
        "ERROR: [youtube] 4290: unexpected extractor failure",
        "Got 429 bytes of unexpected output",
    ],
)
def test_a_429_inside_an_id_or_number_is_not_a_rate_limit(detail: str) -> None:
    assert classify_transcript_error(detail) is FailureType.PROVIDER_ERROR


@pytest.mark.parametrize(
    "detail",
    [
        "HTTP Error 429: Too Many Requests",
        "status code 429",
        "ERROR: Request failed with status 429",
        "You have been rate-limited by YouTube",
        "rate limit exceeded",
    ],
)
def test_real_rate_limit_shapes_are_still_detected(detail: str) -> None:
    assert classify_transcript_error(detail) is FailureType.RATE_LIMITED


@pytest.mark.parametrize(
    "detail",
    [
        "ERROR: [youtube] xprivatex: unexpected failure",
        "ERROR: [youtube] deletedvid1: unexpected failure",
        "ERROR: [youtube] Odnsx_sslY: unexpected failure",
    ],
)
def test_substrings_inside_video_ids_do_not_match(detail: str) -> None:
    assert classify_transcript_error(detail) is FailureType.PROVIDER_ERROR


def test_no_subtitles_and_disabled_subtitles_have_distinct_reasons() -> None:
    none_at_all = classify_transcript_error("There are no subtitles for this video")
    disabled = classify_transcript_error("Subtitles are disabled for this video")

    assert none_at_all is FailureType.NO_USABLE_LANGUAGE
    assert disabled is FailureType.TRANSCRIPTS_DISABLED
    assert human_reason(none_at_all) != human_reason(disabled)


def test_a_standalone_dns_or_ssl_failure_is_a_network_error() -> None:
    assert classify_transcript_error("DNS lookup failed") is FailureType.NETWORK_ERROR
    assert classify_transcript_error("SSL: CERTIFICATE_VERIFY_FAILED") is FailureType.NETWORK_ERROR



@pytest.mark.parametrize(("fn", "detail", "expected"), CASES)
def test_classify(fn: object, detail: str, expected: FailureType) -> None:
    assert fn(detail) is expected  # type: ignore[operator]
    assert kind_of(expected) is FAILURE_KINDS[expected]


def test_private_matches_before_generic_deleted_text() -> None:
    """'Private video' must not fall through to the broader 'video unavailable' rule."""
    assert classify_transcript_error("Private video") is FailureType.VIDEO_PRIVATE


def test_matching_is_case_insensitive() -> None:
    assert classify_transcript_error("PRIVATE VIDEO") is FailureType.VIDEO_PRIVATE
    assert classify_transcript_error("Rate Limit Exceeded") is FailureType.RATE_LIMITED


@pytest.mark.parametrize(
    "expected",
    [
        FailureType.VIDEO_PRIVATE,
        FailureType.VIDEO_DELETED,
        FailureType.AGE_RESTRICTED,
        FailureType.REGION_BLOCKED,
        FailureType.MEMBERS_ONLY,
        FailureType.TRANSCRIPTS_DISABLED,
        FailureType.NO_USABLE_LANGUAGE,
        FailureType.TRANSCRIPT_TOO_LARGE,
        FailureType.RATE_LIMITED,
        FailureType.NETWORK_ERROR,
    ],
)
def test_classified_type_agrees_with_kind_of(expected: FailureType) -> None:
    assert kind_of(expected) is FAILURE_KINDS[expected]


def test_permanent_failures_do_not_auto_retry() -> None:
    for failure_type in (
        FailureType.VIDEO_PRIVATE,
        FailureType.VIDEO_DELETED,
        FailureType.AGE_RESTRICTED,
        FailureType.REGION_BLOCKED,
        FailureType.MEMBERS_ONLY,
        FailureType.TRANSCRIPTS_DISABLED,
        FailureType.NO_USABLE_LANGUAGE,
        FailureType.TRANSCRIPT_TOO_LARGE,
    ):
        assert kind_of(failure_type) is FailureKind.PERMANENT


def test_transient_failures_auto_retry() -> None:
    for failure_type in (
        FailureType.RATE_LIMITED,
        FailureType.NETWORK_ERROR,
        FailureType.PROVIDER_ERROR,
        FailureType.OUTPUT_MALFORMED,
    ):
        assert kind_of(failure_type) is FailureKind.TRANSIENT


def test_classify_error_returns_the_full_triple() -> None:
    failure_type, kind, reason = classify_error("Subtitles are disabled for this video")
    assert failure_type is FailureType.TRANSCRIPTS_DISABLED
    assert kind is kind_of(FailureType.TRANSCRIPTS_DISABLED)
    assert reason == human_reason(FailureType.TRANSCRIPTS_DISABLED)


def test_unknown_error_falls_back_to_a_transient_provider_error() -> None:
    unknown = "ERROR: something completely unexpected happened at yt-dlp internals"
    assert classify_transcript_error(unknown) is FailureType.PROVIDER_ERROR
    assert kind_of(classify_transcript_error(unknown)) is FailureKind.TRANSIENT


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_blank_detail_is_not_classified_as_a_permanent_failure(blank: str) -> None:
    assert kind_of(classify_transcript_error(blank)) is FailureKind.TRANSIENT


def test_every_failure_type_has_a_human_reason() -> None:
    for failure_type in FailureType:
        reason = human_reason(failure_type)
        assert reason, failure_type
        assert "_" not in reason, f"{failure_type}: {reason}"
        assert reason == reason.strip()
        assert reason[0].isupper()
        assert reason.endswith(".")


def test_human_reason_reads_as_a_sentence_not_an_enum() -> None:
    assert human_reason(FailureType.TRANSCRIPTS_DISABLED) == (
        "Transcripts are disabled by the uploader."
    )
    assert human_reason(FailureType.VIDEO_PRIVATE) != FailureType.VIDEO_PRIVATE.value


def test_human_reason_never_leaks_provider_text() -> None:
    raw = "ERROR: unable to download API page: HTTP Error 429: Too Many Requests"
    reason = human_reason(classify_transcript_error(raw))
    assert "429" not in reason
    assert "ERROR" not in reason
    assert "download" not in reason


def test_classify_output_error_defaults_to_malformed() -> None:
    assert classify_output_error("Expecting value: line 1 column 1 (char 0)") is (
        FailureType.OUTPUT_MALFORMED
    )
    assert classify_output_error("") is FailureType.OUTPUT_MALFORMED


def test_classify_output_error_detects_a_rate_limit_surfacing_as_text() -> None:
    assert classify_output_error("HTTP Error 429: Too Many Requests") is FailureType.RATE_LIMITED


def test_classify_playlist_error() -> None:
    assert classify_playlist_error("Playlist does not exist") is FailureType.PLAYLIST_UNAVAILABLE
    assert classify_playlist_error("Invalid URL") is FailureType.INVALID_URL
    # Falls back to the per-video rules when it is really a video-level failure.
    assert classify_playlist_error("This video is private") is FailureType.VIDEO_PRIVATE


def test_classify_drive_error() -> None:
    assert classify_drive_error("403 insufficientPermissions") is FailureType.DRIVE_ERROR
    assert kind_of(FailureType.DRIVE_ERROR) is FailureKind.TRANSIENT


def test_safe_diagnostic_keeps_only_the_first_line() -> None:
    detail = "ERROR: Private video\nhttps://trace.example/internal?token=secret\nmore noise"
    assert safe_diagnostic(detail) == "ERROR: Private video"


def test_safe_diagnostic_truncates_and_bounds_length() -> None:
    detail = "x" * 500
    truncated = safe_diagnostic(detail, limit=32)
    assert len(truncated) == 32
    assert truncated.endswith("...")
    assert truncated == "x" * 29 + "..."


def test_safe_diagnostic_handles_blank_input() -> None:
    assert safe_diagnostic("") == ""
    assert safe_diagnostic("   \n  ") == ""


def test_safe_diagnostic_collapses_inner_whitespace() -> None:
    assert safe_diagnostic("ERROR:   private\tvideo  \nnext line") == "ERROR: private video"


def test_safe_diagnostic_never_returns_a_whole_transcript() -> None:
    transcript = "line one of a transcript\n" + "lorem ipsum " * 100
    assert len(safe_diagnostic(transcript)) <= 200


@pytest.mark.parametrize(
    "detail",
    [
        # yt-dlp's wording when YouTube throttles the IP: it says "unavailable" but also
        # "try again later" / "rate-limited", so it must not become a permanent deletion.
        "ERROR: [youtube] abc123def45: Video unavailable. This content isn't available, "
        "try again later.",
        "ERROR: [youtube] abc123def45: Video unavailable. The current session has been "
        "rate-limited by YouTube for up to an hour.",
    ],
)
def test_throttling_disguised_as_unavailable_is_rate_limited(detail: str) -> None:
    assert classify_transcript_error(detail) is FailureType.RATE_LIMITED


def test_a_plain_unavailable_video_is_still_permanent() -> None:
    detail = "ERROR: [youtube] abc123def45: Video unavailable. This video has been removed."
    assert classify_transcript_error(detail) is FailureType.VIDEO_DELETED
