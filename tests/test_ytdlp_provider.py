"""Tests for the async yt-dlp transcript provider.

No network and no real yt-dlp extraction: the single boundary
``provider_module.download_subtitles`` is monkeypatched in every test, so the
suite is fast, deterministic and offline.

Covers the PRD section 8 taxonomy surfacing through :class:`TranscriptError`,
the SRT/VTT cleanup, the usage ledger contract, path isolation between
concurrent attempts, and the regression test for the old blocking-sleep defect.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from src.domain.interfaces.usage_ledger import UsageRecord
from src.domain.models.playlist import FailureKind, FailureType, TranscriptError
from src.infrastructure.transcript.ytdlp import provider as provider_module
from src.infrastructure.transcript.ytdlp.provider import (
    YtDlpTranscriptProvider,
    clean_transcript_text,
    safe_detail,
)

VIDEO_ID = "dQw4w9WgXcQ"
OTHER_VIDEO_ID = "9bZkp7q19f0"

SRT_PAYLOAD = """1
00:00:01,000 --> 00:00:04,000
Welcome back to the channel.

2
00:00:04,000 --> 00:00:07,000
Today we look at how pipelines stay responsive.

3
00:00:07,000 --> 00:00:10,000
The trick is to keep the loop free.

4
00:00:10,000 --> 00:00:13,000
That is the whole lesson for today.
"""

VTT_PAYLOAD = """WEBVTT
Kind: captions
Language: en

NOTE this block is a comment

00:00:01.000 --> 00:00:04.000
<c.colorE5E5E5><00:00:01.000>Welcome back</c> to the channel.

00:00:04.000 --> 00:00:07.000
<c>Tom &amp; Jerry</c> made a <v Narrator>cartoon</v>.
"""

AUTO_CAPTION_PAYLOAD = """1
00:00:01,000 --> 00:00:04,000
so today we are

2
00:00:04,000 --> 00:00:07,000
so today we are

3
00:00:07,000 --> 00:00:10,000
so today we are looking at

4
00:00:10,000 --> 00:00:13,000
so today we are looking at

5
00:00:13,000 --> 00:00:16,000
so today we are looking at concurrency
"""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def make_settings(output_folder: Path, **overrides: Any) -> Callable[[str, Any], Any]:
    """A minimal SettingsLoader backed by a dict."""
    values: dict[str, Any] = {
        "transcript_language": "en",
        "transcript_output_folder": str(output_folder),
        "transcript_failure_cooldown_seconds": 120.0,
        "backoff_base_seconds": 5.0,
        "backoff_cap_seconds": 300.0,
        "max_auto_retries": 4,
    }
    values.update(overrides)

    def loader(key_path: str, default: Any = None) -> Any:
        return values.get(key_path, default)

    return loader


class RecordingLedger:
    """Usage ledger that keeps every record it is handed."""

    def __init__(self, *, explode: bool = False) -> None:
        self.records: list[UsageRecord] = []
        self._explode = explode

    async def record_usage(self, record: UsageRecord) -> None:
        if self._explode:
            raise RuntimeError("ledger database is locked")
        self.records.append(record)


def install_download_stub(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[dict[str, Any], list[str]], None],
    available_languages: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Replace the yt-dlp boundaries; return the list options download was called with.

    ``available_languages`` is what the language-listing boundary reports (default: the video
    has no subtitles at all), so no test ever touches the network.
    """
    seen: list[dict[str, Any]] = []

    def stub(options: dict[str, Any], urls: list[str]) -> None:
        seen.append(options)
        handler(options, urls)

    monkeypatch.setattr(provider_module, "download_subtitles", stub)
    monkeypatch.setattr(
        provider_module,
        "list_subtitle_languages",
        lambda options, url: list(available_languages or []),
    )
    return seen


def write_subtitle(
    options: dict[str, Any],
    url: str,
    *,
    language: str,
    payload: str,
    extension: str = "srt",
) -> Path:
    """Mimic yt-dlp writing ``<video id>.<lang>.<ext>`` into its outtmpl dir."""
    outtmpl = str(options["outtmpl"])
    work_dir = Path(outtmpl).parent
    video_id = url.rsplit("=", 1)[-1]
    path = work_dir / f"{video_id}.{language}.{extension}"
    path.write_text(payload, encoding="utf-8")
    return path


def raising_stub(message: str) -> Callable[[dict[str, Any], list[str]], None]:
    def handler(options: dict[str, Any], urls: list[str]) -> None:
        raise RuntimeError(message)

    return handler


def build_provider(tmp_path: Path, **overrides: Any) -> YtDlpTranscriptProvider:
    return YtDlpTranscriptProvider(make_settings(tmp_path / "transcripts", **overrides))


# ---------------------------------------------------------------------------
# 1. success path
# ---------------------------------------------------------------------------
async def test_fetch_returns_cleaned_text_language_and_usage_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = RecordingLedger()
    seen = install_download_stub(
        monkeypatch,
        lambda options, urls: write_subtitle(options, urls[0], language="en", payload=SRT_PAYLOAD),
    )
    provider = YtDlpTranscriptProvider(make_settings(tmp_path / "transcripts"), ledger)

    result = await provider.fetch_transcript(VIDEO_ID)

    assert result.video_id == VIDEO_ID
    assert result.language == "en"
    assert result.cleaned_text == SRT_CLEANED
    assert result.raw_srt == SRT_PAYLOAD
    assert result.byte_size == len(SRT_CLEANED.encode("utf-8"))
    assert result.content_hash and len(result.content_hash) == 64
    assert Path(result.artifact_path).is_file()

    assert len(ledger.records) == 1
    record = ledger.records[0]
    assert record.provider == "ytdlp"
    assert record.operation == "fetch_transcript"
    assert record.video_id == VIDEO_ID
    assert record.total_tokens == 0 and record.cost_usd == 0.0

    # Requested language first, English as the fallback track.
    assert seen[0]["subtitleslangs"] == ["en"]
    assert seen[0]["skip_download"] is True
    assert seen[0]["writesubtitles"] is True
    assert seen[0]["writeautomaticsub"] is True
    assert seen[0]["retries"] == 0


async def test_requested_language_is_preferred_and_english_is_the_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = install_download_stub(
        monkeypatch,
        lambda options, urls: write_subtitle(options, urls[0], language="en", payload=SRT_PAYLOAD),
    )
    provider = build_provider(tmp_path)

    # Only an English track exists, so the delivered language must reflect the
    # track actually used rather than the requested one (PRD section 20).
    result = await provider.fetch_transcript(VIDEO_ID, language="de")

    assert seen[0]["subtitleslangs"] == ["de", "en"]
    assert result.language == "en"
    assert result.cleaned_text == SRT_CLEANED


async def test_prefers_the_requested_language_track_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(options: dict[str, Any], urls: list[str]) -> None:
        write_subtitle(options, urls[0], language="en", payload=SRT_PAYLOAD)
        write_subtitle(
            options,
            urls[0],
            language="de",
            payload=SRT_PAYLOAD.replace("Welcome back", "Willkommen zurück"),
        )

    install_download_stub(monkeypatch, handler)
    provider = build_provider(tmp_path)

    result = await provider.fetch_transcript(VIDEO_ID, language="de")

    assert result.language == "de"
    assert "Willkommen zurück" in result.cleaned_text


async def test_no_ledger_means_no_usage_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_download_stub(
        monkeypatch,
        lambda options, urls: write_subtitle(options, urls[0], language="en", payload=SRT_PAYLOAD),
    )
    provider = build_provider(tmp_path)

    result = await provider.fetch_transcript(VIDEO_ID)

    assert result.cleaned_text == SRT_CLEANED


# ---------------------------------------------------------------------------
# 2-4. subtitle parsing
# ---------------------------------------------------------------------------
def test_srt_parsing_strips_cue_indices_and_timestamps() -> None:
    cleaned = clean_transcript_text(SRT_PAYLOAD)

    assert cleaned == SRT_CLEANED
    for marker in ("00:00:01,000", "-->", "1\n"):
        assert marker not in cleaned


def test_srt_paragraphs_are_joined_into_readable_prose() -> None:
    cleaned = clean_transcript_text(SRT_PAYLOAD)

    # Four sentences -> one paragraph, wrapped lines joined with a space.
    assert "\n" not in cleaned
    assert "Welcome back to the channel. Today we look" in cleaned
    assert "  " not in cleaned


def test_vtt_parsing_strips_header_and_inline_tags() -> None:
    cleaned = clean_transcript_text(VTT_PAYLOAD)

    assert "WEBVTT" not in cleaned
    assert "Kind: captions" not in cleaned
    assert "this block is a comment" not in cleaned
    assert "00:00:01.000" not in cleaned
    assert "<c" not in cleaned and "<v Narrator>" not in cleaned
    assert "&amp;" not in cleaned
    assert cleaned == "Welcome back to the channel. Tom & Jerry made a cartoon."


def test_auto_caption_repetition_is_collapsed() -> None:
    cleaned = clean_transcript_text(AUTO_CAPTION_PAYLOAD)

    assert cleaned.count("so today we are") == 1
    assert cleaned == "so today we are looking at concurrency"


def test_capitalized_prose_starting_with_note_is_not_treated_as_a_comment() -> None:
    payload = "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nNote that the loop stays free.\n"

    assert clean_transcript_text(payload) == "Note that the loop stays free."


def test_numeric_caption_is_kept_when_it_is_not_a_cue_index() -> None:
    payload = "1\n00:00:01,000 --> 00:00:04,000\n42\n"

    assert clean_transcript_text(payload) == "42"


# ---------------------------------------------------------------------------
# 5-6. failure taxonomy
# ---------------------------------------------------------------------------
PERMANENT_FAILURES: list[tuple[str, FailureType]] = [
    (
        "ERROR: [youtube] abc: Private video. Sign in if you've been granted access",
        FailureType.VIDEO_PRIVATE,
    ),
    (
        "ERROR: [youtube] abc: Video unavailable. This video has been removed by the uploader",
        FailureType.VIDEO_DELETED,
    ),
    (
        "ERROR: [youtube] abc: Sign in to confirm your age. This video may be inappropriate",
        FailureType.AGE_RESTRICTED,
    ),
    (
        "The uploader has not made this video available in your country",
        FailureType.REGION_BLOCKED,
    ),
    (
        "Join this channel to get access to members-only content like this video",
        FailureType.MEMBERS_ONLY,
    ),
    ("Subtitles are disabled for this video", FailureType.TRANSCRIPTS_DISABLED),
]


@pytest.mark.parametrize(("detail", "expected"), PERMANENT_FAILURES)
async def test_permanent_failures_raise_permanent_transcript_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, detail: str, expected: FailureType
) -> None:
    ledger = RecordingLedger()
    install_download_stub(monkeypatch, raising_stub(detail))
    provider = YtDlpTranscriptProvider(make_settings(tmp_path / "transcripts"), ledger)

    with pytest.raises(TranscriptError) as excinfo:
        await provider.fetch_transcript(VIDEO_ID)

    error = excinfo.value
    assert error.failure_type is expected
    assert error.failure_kind is FailureKind.PERMANENT
    assert "_" not in error.reason
    assert error.reason.endswith(".")
    assert str(expected.value) in str(error)
    assert error.diagnostic
    # A permanent failure is still recorded once, and never retried in-provider.
    assert len(ledger.records) == 1


TRANSIENT_FAILURES: list[tuple[str, FailureType]] = [
    (
        "ERROR: unable to download API page: HTTP Error 429: Too Many Requests",
        FailureType.RATE_LIMITED,
    ),
    ("ERROR: unable to download video data: <urlopen error timed out>", FailureType.NETWORK_ERROR),
    ("ERROR: something entirely unexpected happened", FailureType.PROVIDER_ERROR),
]


@pytest.mark.parametrize(("detail", "expected"), TRANSIENT_FAILURES)
async def test_transient_failures_raise_transient_transcript_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, detail: str, expected: FailureType
) -> None:
    install_download_stub(monkeypatch, raising_stub(detail))
    provider = build_provider(tmp_path)

    with pytest.raises(TranscriptError) as excinfo:
        await provider.fetch_transcript(VIDEO_ID)

    assert excinfo.value.failure_type is expected
    assert excinfo.value.failure_kind is FailureKind.TRANSIENT


async def test_missing_subtitle_file_is_a_classified_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_download_stub(monkeypatch, lambda options, urls: None)
    provider = build_provider(tmp_path)

    with pytest.raises(TranscriptError) as excinfo:
        await provider.fetch_transcript(VIDEO_ID)

    assert excinfo.value.failure_type is FailureType.NO_USABLE_LANGUAGE
    assert excinfo.value.failure_kind is FailureKind.PERMANENT


async def test_a_non_default_language_is_used_as_is_and_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PRD section 20: neither the requested language nor English exists, but Hindi does."""

    def handler(options: dict[str, Any], urls: list[str]) -> None:
        if options["subtitleslangs"] == ["hi"]:
            write_subtitle(options, urls[0], language="hi", payload=SRT_PAYLOAD)

    seen = install_download_stub(monkeypatch, handler, available_languages=["hi"])
    provider = build_provider(tmp_path)

    result = await provider.fetch_transcript(VIDEO_ID)

    assert result.language == "hi"
    assert "Welcome back to the channel." in result.cleaned_text
    assert [call["subtitleslangs"] for call in seen] == [["en"], ["hi"]]


async def test_fallback_prefers_the_original_language_automatic_track(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(options: dict[str, Any], urls: list[str]) -> None:
        if options["subtitleslangs"] == ["ja-orig"]:
            write_subtitle(options, urls[0], language="ja-orig", payload=SRT_PAYLOAD)

    install_download_stub(
        monkeypatch, handler, available_languages=["fr", "de", "ja-orig"]
    )
    provider = build_provider(tmp_path)

    result = await provider.fetch_transcript(VIDEO_ID)

    assert result.language == "ja-orig"


def test_pick_fallback_language() -> None:
    assert provider_module.pick_fallback_language([]) is None
    assert provider_module.pick_fallback_language(["hi", "de"]) == "hi"
    assert provider_module.pick_fallback_language(["hi", "es-orig"]) == "es-orig"


async def test_no_subtitles_at_all_is_no_usable_language_not_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_download_stub(monkeypatch, lambda options, urls: None, available_languages=[])
    provider = build_provider(tmp_path)

    with pytest.raises(TranscriptError) as excinfo:
        await provider.fetch_transcript(VIDEO_ID)

    assert excinfo.value.failure_type is FailureType.NO_USABLE_LANGUAGE


async def test_uploader_disabled_subtitles_stay_a_distinct_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(options: dict[str, Any], urls: list[str]) -> None:
        raise RuntimeError("ERROR: Subtitles are disabled for this video")

    install_download_stub(monkeypatch, handler)
    provider = build_provider(tmp_path)

    with pytest.raises(TranscriptError) as excinfo:
        await provider.fetch_transcript(VIDEO_ID)

    assert excinfo.value.failure_type is FailureType.TRANSCRIPTS_DISABLED


async def test_unreadable_subtitle_file_is_output_malformed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_download_stub(
        monkeypatch,
        lambda options, urls: write_subtitle(
            options, urls[0], language="en", payload="WEBVTT\n\nNOTE nothing here\n"
        ),
    )
    provider = build_provider(tmp_path)

    with pytest.raises(TranscriptError) as excinfo:
        await provider.fetch_transcript(VIDEO_ID)

    assert excinfo.value.failure_type is FailureType.OUTPUT_MALFORMED
    assert excinfo.value.failure_kind is FailureKind.TRANSIENT


async def test_provider_does_not_retry_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transient error surfaces immediately; the worker owns the retry."""
    calls = install_download_stub(
        monkeypatch, raising_stub("ERROR: unable to download API page: HTTP Error 429")
    )
    provider = build_provider(tmp_path)

    with pytest.raises(TranscriptError):
        await provider.fetch_transcript(VIDEO_ID)

    assert len(calls) == 1


# ---------------------------------------------------------------------------
# 7. the event loop must never be blocked
# ---------------------------------------------------------------------------
async def test_slow_download_does_not_block_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def slow_handler(options: dict[str, Any], urls: list[str]) -> None:
        # Blocking sleep, exactly like a real synchronous yt-dlp download.
        time.sleep(0.4)
        write_subtitle(options, urls[0], language="en", payload=SRT_PAYLOAD)

    install_download_stub(monkeypatch, slow_handler)
    provider = build_provider(tmp_path)

    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.05)
            ticks += 1

    ticker_task = asyncio.create_task(ticker())
    try:
        result = await provider.fetch_transcript(VIDEO_ID)
    finally:
        ticker_task.cancel()

    assert result.cleaned_text == SRT_CLEANED
    # A blocked loop would let the ticker run at most once, if at all.
    assert ticks >= 4, f"event loop was starved by the download (only {ticks} ticks)"


# ---------------------------------------------------------------------------
# 8. concurrent fetches never collide on the output path
# ---------------------------------------------------------------------------
async def test_concurrent_fetches_use_distinct_output_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(options: dict[str, Any], urls: list[str]) -> None:
        write_subtitle(options, urls[0], language="en", payload=SRT_PAYLOAD)

    install_download_stub(monkeypatch, handler)
    provider = build_provider(tmp_path)

    results = await provider.fetch_transcripts([VIDEO_ID, OTHER_VIDEO_ID, VIDEO_ID])

    paths = [result.artifact_path for result in results]
    assert len(set(paths)) == len(paths)
    assert all(Path(path).is_file() for path in paths)
    assert [result.video_id for result in results] == [VIDEO_ID, OTHER_VIDEO_ID, VIDEO_ID]
    # Working directories are cleaned up; only the final artifacts remain.
    leftovers = [p for p in (tmp_path / "transcripts").iterdir() if p.is_dir()]
    assert leftovers == []


async def test_a_later_fetch_does_not_clobber_a_previous_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_download_stub(
        monkeypatch,
        lambda options, urls: write_subtitle(options, urls[0], language="en", payload=SRT_PAYLOAD),
    )
    provider = build_provider(tmp_path)

    first = await provider.fetch_transcript(VIDEO_ID)
    first_contents = Path(first.artifact_path).read_text(encoding="utf-8")
    second = await provider.fetch_transcript(VIDEO_ID)

    assert first.artifact_path != second.artifact_path
    assert Path(first.artifact_path).read_text(encoding="utf-8") == first_contents
    assert Path(second.artifact_path).is_file()


async def test_batch_fetch_propagates_the_first_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(options: dict[str, Any], urls: list[str]) -> None:
        if urls[0].endswith(OTHER_VIDEO_ID):
            raise RuntimeError("Subtitles are disabled for this video")
        write_subtitle(options, urls[0], language="en", payload=SRT_PAYLOAD)

    install_download_stub(monkeypatch, handler)
    provider = build_provider(tmp_path)

    with pytest.raises(TranscriptError) as excinfo:
        await provider.fetch_transcripts([VIDEO_ID, OTHER_VIDEO_ID])

    assert excinfo.value.failure_type is FailureType.TRANSCRIPTS_DISABLED


# ---------------------------------------------------------------------------
# 9. the ledger must never break a fetch
# ---------------------------------------------------------------------------
async def test_ledger_failure_does_not_break_a_successful_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_download_stub(
        monkeypatch,
        lambda options, urls: write_subtitle(options, urls[0], language="en", payload=SRT_PAYLOAD),
    )
    provider = YtDlpTranscriptProvider(
        make_settings(tmp_path / "transcripts"), RecordingLedger(explode=True)
    )

    result = await provider.fetch_transcript(VIDEO_ID)

    assert result.cleaned_text == SRT_CLEANED


async def test_ledger_failure_does_not_mask_the_real_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_download_stub(monkeypatch, raising_stub("Subtitles are disabled for this video"))
    provider = YtDlpTranscriptProvider(
        make_settings(tmp_path / "transcripts"), RecordingLedger(explode=True)
    )

    with pytest.raises(TranscriptError) as excinfo:
        await provider.fetch_transcript(VIDEO_ID)

    assert excinfo.value.failure_type is FailureType.TRANSCRIPTS_DISABLED


# ---------------------------------------------------------------------------
# 10. no content and no keys leak into errors
# ---------------------------------------------------------------------------
LEAKY_DETAIL = (
    "ERROR: [youtube] abc: unable to fetch subtitle list "
    "https://www.youtube.com/api/timedtext?v=abc&key=AIzaSySECRETKEY123\n"
    "Authorization: Bearer super.secret.jwt.value\n"
    "response body: LEAKED-TRANSCRIPT-CONTENT-MARKER from the caption file"
)


async def test_error_never_carries_transcript_content_or_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_download_stub(monkeypatch, raising_stub(LEAKY_DETAIL))
    provider = build_provider(tmp_path)

    with pytest.raises(TranscriptError) as excinfo:
        await provider.fetch_transcript(VIDEO_ID)

    error = excinfo.value
    for haystack in (str(error), error.diagnostic or "", error.reason):
        assert "AIzaSySECRETKEY123" not in haystack
        assert "super.secret.jwt.value" not in haystack
        assert "LEAKED-TRANSCRIPT-CONTENT-MARKER" not in haystack


def test_safe_detail_redacts_and_truncates() -> None:
    detail = safe_detail("fetch failed for key=AIzaSySECRETKEY123\n" + "x" * 500)

    assert "AIzaSySECRETKEY123" not in detail
    assert "<redacted>" in detail
    assert len(detail) <= 200
    assert "\n" not in detail


async def test_successful_transcript_content_is_not_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    install_download_stub(
        monkeypatch,
        lambda options, urls: write_subtitle(options, urls[0], language="en", payload=SRT_PAYLOAD),
    )
    provider = build_provider(tmp_path)

    with caplog.at_level("DEBUG", logger=provider_module.logger.name):
        result = await provider.fetch_transcript(VIDEO_ID)

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "Welcome back to the channel" not in logged
    assert result.cleaned_text


# ---------------------------------------------------------------------------
# expected values
# ---------------------------------------------------------------------------
#: What ``clean_srt_content`` produces for SRT_PAYLOAD (four sentences).
SRT_CLEANED = (
    "Welcome back to the channel. Today we look at how pipelines stay responsive. "
    "The trick is to keep the loop free. That is the whole lesson for today."
)


def test_a_relative_output_folder_is_under_the_repo_root_not_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleanup resolves the folder against the repo root; writes must use the same place."""
    from src.config.settings import get_storage_settings, repo_root

    monkeypatch.chdir(tmp_path)
    provider = YtDlpTranscriptProvider(make_settings(Path("transcripts")))

    assert Path(provider._output_folder).resolve() == (repo_root() / "transcripts").resolve()
    assert Path(get_storage_settings()["transcripts_dir"]).resolve() == (
        repo_root() / "transcripts"
    ).resolve()
