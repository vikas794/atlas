"""Async YouTube transcript provider backed by yt-dlp.

This adapter owns exactly one thing: getting a video's subtitles out of yt-dlp
and turning them into readable prose. It deliberately owns *nothing else*:

* **No retries.** A transient failure raises immediately with
  ``failure_kind == TRANSIENT``; the worker (not this module) decides whether
  and when to retry, using ``src.domain.services.backoff``. See PRD section 8.
* **No blocking.** ``YoutubeDL`` is a synchronous library, so it is pushed to a
  worker thread with :func:`asyncio.to_thread`. Nothing in this module ever
  calls ``time.sleep``, so a slow download cannot stall the whole pipeline.
* **No silent empty results.** Every failure raises :class:`TranscriptError`
  carrying a classified :class:`FailureType`, so the caller can tell "no
  subtitles" from "rate limited" from "the process exploded".
* **No secrets in logs.** Neither the transcript text nor any API key is ever
  logged, stored on the exception, or emitted to the usage ledger; only the
  truncated provider diagnostic travels in ``TranscriptError.diagnostic``.

Test isolation: the whole yt-dlp boundary is the single module-level function
:func:`download_subtitles`, which is what the test suite monkeypatches. No real
network access and no real yt-dlp extraction happens in the tests.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final
from uuid import uuid4

from yt_dlp import YoutubeDL

from src.config.settings import SettingsLoader, repo_root
from src.domain.interfaces.transcript_provider import TranscriptProviderPort, TranscriptResult
from src.domain.interfaces.usage_ledger import UsageLedgerPort, UsageRecord
from src.domain.models.playlist import FailureType, TranscriptError
from src.domain.services.failure_classifier import (
    classify_transcript_error,
    human_reason,
    safe_diagnostic,
)
from src.shared.redaction import redact_text
from src.shared.text import clean_srt_content, ensure_output_folder, sha256_text

logger = logging.getLogger(__name__)

#: Watch URL template; the video id is already validated upstream.
WATCH_URL: Final[str] = "https://www.youtube.com/watch?v={video_id}"

#: Provider name used in the usage ledger.
PROVIDER_NAME: Final[str] = "ytdlp"
#: Operation name used in the usage ledger.
OPERATION_NAME: Final[str] = "fetch_transcript"

#: Subtitle containers yt-dlp may write with ``subtitlesformat = "srt"``.
_SUBTITLE_SUFFIXES: Final[tuple[str, ...]] = (".srt", ".vtt")

#: "00:00:01,000 --> 00:00:04,000" (SRT) and "00:00:01.000 --> 00:00:04.000" (VTT).
_CUE_TIMING: Final[re.Pattern[str]] = re.compile(
    r"^\d{1,3}:\d{2}:\d{2}[.,]\d{1,3}\s*-->\s*\d{1,3}:\d{2}:\d{2}[.,]\d{1,3}"
)
#: A cue index ("12"). Only treated as an index when a timing line follows it.
_CUE_INDEX: Final[re.Pattern[str]] = re.compile(r"^\d+$")
#: Any WebVTT inline tag: <c>, <00:00:01.000>, <v Speaker>, </c> ...
_INLINE_TAG: Final[re.Pattern[str]] = re.compile(r"<[^>]*>")
#: "WEBVTT" / "WEBVTT - description" header. Matched case-sensitively so a
#: caption that merely mentions webvtt is never mistaken for the header.
_WEBVTT_HEADER: Final[re.Pattern[str]] = re.compile(r"^WEBVTT\b")
#: WebVTT blocks whose body must be dropped. Uppercase-only on purpose: a
#: caption reading "Note that ..." is prose, not a comment block.
_NOTE_BLOCK: Final[re.Pattern[str]] = re.compile(r"^NOTE\b")
_SKIPPED_BLOCK_HEADS: Final[frozenset[str]] = frozenset({"NOTE", "STYLE", "REGION"})


def download_subtitles(options: dict[str, Any], urls: list[str]) -> None:
    """Synchronous yt-dlp boundary.

    Blocking by design: callers must invoke it through :func:`asyncio.to_thread`.
    Keeping it a free function with no ``self`` makes it a one-line monkeypatch
    seam for the test suite.
    """
    with YoutubeDL(options) as ydl:
        ydl.download(urls)


def list_subtitle_languages(options: dict[str, Any], url: str) -> list[str]:
    """Synchronous yt-dlp boundary: which subtitle languages does this video have?

    Manual tracks come first, then automatic captions. Like :func:`download_subtitles` this
    blocks, so callers use :func:`asyncio.to_thread`, and it is a one-line monkeypatch seam.
    """
    with YoutubeDL({**options, "skip_download": True}) as ydl:
        info = ydl.extract_info(url, download=False) or {}
    manual = [code for code in (info.get("subtitles") or {}) if code != "live_chat"]
    automatic = [
        code
        for code in (info.get("automatic_captions") or {})
        if code != "live_chat" and code not in manual
    ]
    return [*manual, *automatic]


def pick_fallback_language(available: list[str]) -> str | None:
    """Choose which of the available subtitle languages to use when neither the requested
    language nor English exists (PRD section 20: used as is, with the language recorded).

    Manual tracks keep their order (they are listed first); among automatic captions a
    ``-orig`` track (the video's original spoken language) beats the long tail of machine
    translations. Returns ``None`` when the video has no subtitles at all.
    """
    if not available:
        return None
    for code in available:
        if code.endswith("-orig"):
            return code
    return available[0]


def _language_preference(requested: str) -> list[str]:
    """Ordered subtitle languages to request, most preferred first.

    The requested language always comes first; English is the fallback so an
    auto-generated non-English track is still usable rather than failing the
    video outright (PRD section 20: the transcript is used as is, with the
    language recorded).
    """
    ordered: list[str] = []
    for code in (requested, "en"):
        code = (code or "").strip()
        if code and code not in ordered:
            ordered.append(code)
    return ordered or ["en"]


def _next_line_is_cue_timing(lines: list[str], start: int) -> bool:
    """Whether the next non-blank line at/after ``start`` is a cue timing line."""
    for candidate in lines[start:]:
        stripped = candidate.strip()
        if not stripped:
            continue
        return _CUE_TIMING.match(stripped) is not None
    return False


def clean_transcript_text(raw: str) -> str:
    """Turn an SRT/VTT payload into readable prose paragraphs.

    Handles both container dialects without a third-party parser:

    * drops the ``WEBVTT`` header, cue indices and ``start --> end`` timings;
    * strips inline tags such as ``<c.colorE5E5E5>`` and ``<00:00:01.000>``;
    * drops ``NOTE`` / ``STYLE`` / ``REGION`` blocks;
    * unescapes HTML entities (``&amp;`` -> ``&``);
    * collapses the rolling line repetition that auto-generated captions emit;
    * normalises whitespace and groups the result into paragraphs.

    Deterministic and dependency-free by design.
    """
    normalized = raw.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    source_lines = normalized.split("\n")

    kept: list[str] = []
    in_header = False
    skip_block = False
    for index, line in enumerate(source_lines):
        stripped = line.strip()
        if not stripped or _CUE_TIMING.match(stripped):
            # A blank line ends a header/comment block; a timing line is a hard
            # boundary, which keeps a header-less file from being swallowed.
            in_header = False
            skip_block = False
            continue
        if in_header or skip_block:
            continue
        if _WEBVTT_HEADER.match(stripped):
            in_header = True
            continue
        if stripped in _SKIPPED_BLOCK_HEADS or _NOTE_BLOCK.match(stripped):
            skip_block = True
            continue
        if _CUE_INDEX.match(stripped) and _next_line_is_cue_timing(source_lines, index + 1):
            continue

        text = html.unescape(_INLINE_TAG.sub("", stripped)).strip()
        if text:
            kept.append(text)

    prose = " ".join(_collapse_caption_repeats(kept)).strip()
    if not prose:
        return ""
    # ``clean_srt_content`` owns the paragraphing policy shared with the rest of
    # the app; it can only ever return "" for prose it dislikes entirely, and
    # losing content to it is worse than losing the paragraph break.
    return clean_srt_content(prose) or prose


def _collapse_caption_repeats(lines: list[str]) -> list[str]:
    """Undo the rolling repetition that auto-generated captions emit.

    YouTube's auto track re-prints the visible line on every cue while a
    sentence scrolls, so consecutive cues are either identical or the same text
    with more words appended. Identical cues collapse; a growing cue replaces
    the shorter one it extends. A line that already ends a sentence is never
    replaced, so two genuinely different sentences that share an opening stay.
    """
    kept: list[str] = []
    for text in lines:
        if not kept:
            kept.append(text)
            continue
        previous = kept[-1]
        if _normalized_key(text) == _normalized_key(previous):
            continue
        if not previous.endswith((".", "!", "?")) and _normalized_key(text).startswith(
            _normalized_key(previous)
        ):
            kept[-1] = text
            continue
        kept.append(text)
    return kept


def _normalized_key(text: str) -> str:
    """Comparison key for the auto-caption repetition check."""
    return " ".join(text.split()).casefold()


def redact_secrets(detail: str) -> str:
    """Blank out credential-shaped substrings in a raw provider message.

    yt-dlp echoes the URL it was fetching on failure, and YouTube query strings
    carry API keys. Delegates to the shared helper so every layer redacts alike.
    """
    return redact_text(detail)


def safe_detail(detail: str) -> str:
    """Single redacted line of provider text, truncated, for logs only."""
    return safe_diagnostic(redact_secrets(detail))


class YtDlpTranscriptProvider(TranscriptProviderPort):
    """Fetch YouTube transcripts as cleaned prose, one video per call.

    Public surface::

        provider = YtDlpTranscriptProvider(settings, usage_ledger)
        result = await provider.fetch_transcript("dQw4w9WgXcQ")   # raises TranscriptError
        batch = await provider.fetch_transcripts(["id1", "id2"])

    ``result.language`` is the language actually delivered (which may differ
    from the requested one when a fallback track is used), ``result.raw_srt``
    holds the untouched subtitle payload, and ``result.cleaned_text`` holds the
    prose that becomes the stored transcript (``content_hash`` and ``byte_size``
    both describe the cleaned text).
    """

    def __init__(
        self,
        settings: SettingsLoader,
        usage_ledger: UsageLedgerPort | None = None,
    ) -> None:
        self._settings = settings
        self._usage_ledger = usage_ledger
        self._default_language = str(settings("transcript_language", "en") or "en")
        # Relative to the repo root, like the transcript cleanup that deletes these
        # files (get_storage_settings); the working directory must not matter.
        folder = Path(str(settings("transcript_output_folder", "transcripts") or "transcripts"))
        if not folder.is_absolute():
            folder = repo_root() / folder
        self._output_folder = ensure_output_folder(str(folder))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def fetch_transcript(
        self,
        video_id: str,
        language: str | None = None,
    ) -> TranscriptResult:
        """Fetch and clean the transcript for one video.

        Raises :class:`TranscriptError` for every failure, classified via
        :func:`classify_transcript_error`. No retry happens here.
        """
        requested = (language or self._default_language).strip() or self._default_language
        languages = _language_preference(requested)
        url = WATCH_URL.format(video_id=video_id)

        work_dir = Path(
            tempfile.mkdtemp(prefix=f"ytdlp-{_safe_name(video_id)}-", dir=self._output_folder)
        )
        try:
            options = self._ydl_options(languages, work_dir)
            try:
                # Never block the event loop: yt-dlp is synchronous and slow.
                await asyncio.to_thread(download_subtitles, options, [url])
            except Exception as error:
                detail = str(error) or type(error).__name__
                logger.debug("yt-dlp download failed for %s: %s", video_id, safe_detail(detail))
                raise _transcript_error(detail) from error

            subtitle_path = self._locate_subtitle(work_dir, languages)
            if subtitle_path is None:
                # Neither the requested language nor English exists. If *any* subtitle track
                # exists, use it as is and record its language (PRD section 20); only a video
                # with no subtitles at all fails (NO_USABLE_LANGUAGE).
                subtitle_path = await self._fetch_fallback_subtitle(url, languages, work_dir)

            raw = subtitle_path.read_text(encoding="utf-8", errors="replace")
            cleaned = clean_transcript_text(raw)
            if not cleaned:
                raise TranscriptError(
                    FailureType.OUTPUT_MALFORMED,
                    human_reason(FailureType.OUTPUT_MALFORMED),
                    diagnostic=safe_detail(
                        f"Subtitle file had no readable text: {subtitle_path.name}"
                    ),
                )

            delivered_language = _language_from_filename(subtitle_path) or languages[0]
            artifact_path = self._persist_artifact(video_id, subtitle_path)

            result = TranscriptResult(
                video_id=video_id,
                language=delivered_language,
                raw_srt=raw,
                cleaned_text=cleaned,
                content_hash=sha256_text(cleaned),
                artifact_path=artifact_path,
                byte_size=len(cleaned.encode("utf-8")),
            )
            await self._emit_usage(video_id)
            logger.info(
                "Fetched transcript for %s in %s (%d bytes)",
                video_id,
                delivered_language,
                result.byte_size,
            )
            return result
        except TranscriptError:
            await self._emit_usage(video_id)
            raise
        except Exception as error:
            # Defensive: nothing escapes this method unclassified.
            detail = str(error) or type(error).__name__
            logger.debug("Unexpected transcript failure for %s: %s", video_id, safe_detail(detail))
            await self._emit_usage(video_id)
            raise _transcript_error(detail) from error
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    async def fetch_transcripts(
        self,
        video_ids: list[str],
        language: str | None = None,
    ) -> list[TranscriptResult]:
        """Fetch several videos concurrently, preserving input order.

        Failures are not swallowed: every video is attempted, then the first
        error in input order is re-raised so the caller sees a classified
        failure. The worker normally drives ``fetch_transcript`` itself so it
        can apply its own concurrency cap and per-video error handling.
        """
        if not video_ids:
            return []
        outcomes = await asyncio.gather(
            *(self.fetch_transcript(video_id, language) for video_id in video_ids),
            return_exceptions=True,
        )
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome
        return [outcome for outcome in outcomes if isinstance(outcome, TranscriptResult)]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _ydl_options(self, languages: list[str], work_dir: Path) -> dict[str, Any]:
        """yt-dlp options for a subtitle-only fetch.

        These are yt-dlp mechanics rather than user-tunable policy, so they are
        fixed here. Retry counts are zero because retries belong to the worker;
        the output template points at a unique per-attempt directory so two
        concurrent fetches can never read each other's subtitle file.
        """
        return {
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": list(languages),
            "subtitlesformat": "srt",
            "outtmpl": str(work_dir / "%(id)s.%(ext)s"),
            "ignoreerrors": False,
            "retries": 0,
            "fragment_retries": 0,
            "extractor_retries": 0,
            "cachedir": False,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "sleep_interval": 1,
            "max_sleep_interval": 3,
        }

    async def _fetch_fallback_subtitle(
        self, url: str, languages: list[str], work_dir: Path
    ) -> Path:
        """Download whichever subtitle language the video does have, or fail with a reason."""
        try:
            available = await asyncio.to_thread(
                list_subtitle_languages, self._ydl_options(languages, work_dir), url
            )
        except Exception as error:
            detail = str(error) or type(error).__name__
            logger.debug("yt-dlp language listing failed: %s", safe_detail(detail))
            raise _transcript_error(detail) from error

        fallback = pick_fallback_language(available)
        if fallback is None:
            raise _transcript_error(
                "No subtitles are available in any requested language: " + ", ".join(languages)
            )

        logger.info("Using fallback subtitle language %s", fallback)
        try:
            await asyncio.to_thread(
                download_subtitles, self._ydl_options([fallback], work_dir), [url]
            )
        except Exception as error:
            detail = str(error) or type(error).__name__
            raise _transcript_error(detail) from error

        path = self._locate_subtitle(work_dir, [fallback])
        if path is None:
            raise _transcript_error(
                "No subtitles are available in any requested language: " + ", ".join(languages)
            )
        return path

    def _locate_subtitle(self, work_dir: Path, languages: list[str]) -> Path | None:
        """Pick the best subtitle file yt-dlp wrote for this attempt.

        Candidates are ranked by the requested language order, so a track in the
        requested language always wins over the English fallback and an unknown
        language never beats a known one.
        """
        candidates: list[tuple[int, Path]] = []
        for path in sorted(work_dir.glob("*")):
            if path.suffix.lower() not in _SUBTITLE_SUFFIXES:
                continue
            code = _language_from_filename(path)
            rank = languages.index(code) if code in languages else len(languages)
            candidates.append((rank, path))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1].name))
        return candidates[0][1]

    def _persist_artifact(self, video_id: str, subtitle_path: Path) -> str:
        """Move the raw subtitle to a unique per-attempt file and return its path.

        The name carries a random token, so concurrent attempts (including two
        fetches of the *same* video) never overwrite each other and a previous
        good artifact is never clobbered by a later partial one.
        """
        token = uuid4().hex[:12]
        target = Path(self._output_folder) / f"{_safe_name(video_id)}.{token}{subtitle_path.suffix}"
        shutil.move(str(subtitle_path), str(target))
        return str(target)

    async def _emit_usage(self, video_id: str) -> None:
        """Record one usage event for a fetch attempt (success or failure).

        The ledger is bookkeeping, never part of the fetch contract, so this
        never raises: a ledger that explodes must not turn a working fetch into
        a failed one, nor a failed one into a differently-failed one.
        """
        if self._usage_ledger is None:
            return
        try:
            record = UsageRecord(
                timestamp=datetime.now(UTC),
                provider=PROVIDER_NAME,
                operation=OPERATION_NAME,
                model=PROVIDER_NAME,
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                cost_usd=0.0,
                cache_hit=False,
                video_id=video_id,
            )
            await self._usage_ledger.record_usage(record)
        except Exception as error:
            logger.warning("Usage ledger rejected a transcript record: %s", safe_detail(str(error)))


def _transcript_error(detail: str) -> TranscriptError:
    """Build a :class:`TranscriptError` from raw provider text.

    The raw text is kept only as a redacted, truncated, log-safe diagnostic; the
    user-facing reason comes from the taxonomy and never contains provider
    output.
    """
    failure_type = classify_transcript_error(detail)
    return TranscriptError(
        failure_type,
        human_reason(failure_type),
        diagnostic=safe_detail(detail),
    )


def _language_from_filename(path: Path) -> str:
    """Extract the subtitle language from ``<video id>.<lang>.<ext>``.

    Returns "" when the file is not named the way yt-dlp names it, which is how
    the caller knows the language is unknown rather than empty.
    """
    stem = path.name[: -len(path.suffix)] if path.suffix else path.name
    _, separator, remainder = stem.partition(".")
    return remainder.strip() if separator else ""


def _safe_name(value: str) -> str:
    """Filesystem-safe rendering of a video id (defensive, not a validation)."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", value or "").strip("_")
    return cleaned or "video"
