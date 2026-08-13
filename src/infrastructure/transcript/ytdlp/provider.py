from __future__ import annotations

import os
import random
import re
import time
from datetime import UTC
from typing import TYPE_CHECKING

from yt_dlp import YoutubeDL

from src.domain.interfaces.storage import ArtifactStorePort
from src.domain.interfaces.transcript_provider import TranscriptProviderPort, TranscriptResult
from src.domain.interfaces.usage_ledger import UsageLedgerPort, UsageRecord
from src.infrastructure.llm.base import SettingsLoader
from src.utils import ensure_output_folder, sha256_text

if TYPE_CHECKING:
    pass


class YtDlpTranscriptProvider(TranscriptProviderPort):
    """Transcript provider using yt-dlp for YouTube subtitle extraction."""

    def __init__(
        self,
        settings: SettingsLoader,
        usage_ledger: UsageLedgerPort | None = None,
        artifact_store: ArtifactStorePort | None = None,
    ) -> None:
        self._settings = settings
        self._usage_ledger = usage_ledger
        self._artifact_store = artifact_store

        self._output_folder = ensure_output_folder(
            settings("processing.transcripts.output_folder", "transcripts")
        )
        self._language = settings("processing.transcripts.language", "en")
        self._retry_wait_seconds = settings(
            "playlist_quiz.transcript_retry_wait_seconds", [15, 30, 60, 120]
        )
        self._retry_jitter_seconds = float(
            settings("playlist_quiz.transcript_retry_jitter_seconds", 5)
        )
        self._min_delay_between_videos = float(
            settings("playlist_quiz.transcript_min_delay_between_videos", 15)
        )
        self._rate_limit_cooldown_seconds = float(
            settings("playlist_quiz.transcript_rate_limit_cooldown_seconds", 120)
        )
        self._max_retries = len(self._retry_wait_seconds)
        self._delay_between_downloads = settings(
            "playlist_quiz.transcript_delay_between_requests", 4
        )
        self._cooldown_until: float = 0.0

    def _get_ydl_opts(self) -> dict:
        """Get the yt-dlp options configuration."""
        return {
            "skip_download": self._settings("download.skip_download", True),
            "writesubtitles": self._settings("download.write_subtitles", True),
            "writeautomaticsub": self._settings("download.write_automatic_sub", True),
            "subtitleslangs": [self._language],
            "subtitlesformat": self._settings("download.subtitles_format", "srt"),
            "outtmpl": os.path.join(
                self._output_folder,
                self._settings("download.output_template", "%(id)s.%(ext)s"),
            ),
            "ignoreerrors": False,
            "retries": 0,
            "fragment_retries": 0,
            "extractor_retries": 0,
            "sleep_interval": 1,
            "max_sleep_interval": 3,
        }

    @staticmethod
    def _classify_failure(detail: str) -> str:
        """Classify a yt-dlp failure into a terminal reason category."""
        normalized = detail.lower()
        if (
            "members-only" in normalized
            or "members only" in normalized
            or "channel's members" in normalized
        ):
            return "members_only"
        if "no subtitles" in normalized or (
            "subtitle" in normalized and "not available" in normalized
        ):
            return "no_subtitles"
        if "429" in detail or "rate limit" in normalized or "too many requests" in normalized:
            return "rate_limited"
        return "failed"

    @staticmethod
    def _safe_diagnostic(detail: str) -> str:
        """Return a short, log-safe diagnostic for an unexpected failure."""
        stripped = detail.strip()
        first_line = stripped.splitlines()[0] if stripped else "unknown error"
        return first_line[:200]

    @staticmethod
    def _video_id(url: str) -> str | None:
        """Extract the 11-char YouTube video id from a watch/short URL."""
        match = re.search(
            r"(?:v=([\w-]{11})|youtu\.be/([\w-]{11})|/shorts/([\w-]{11}))",
            url,
        )
        if not match:
            return None
        return match.group(1) or match.group(2) or match.group(3)

    def _subtitle_file_exists(self, url: str) -> bool:
        """Check whether any subtitle file was written for the URL's video."""
        video_id = self._video_id(url)
        if not video_id:
            return True
        return any(
            os.path.exists(
                os.path.join(self._output_folder, f"{video_id}.{self._language}.{ext}")
            )
            for ext in ("srt", "vtt")
        )

    def _find_subtitle_file(self, video_id: str) -> str | None:
        """Find the downloaded subtitle file for a video ID."""
        for ext in ("srt", "vtt"):
            path = os.path.join(self._output_folder, f"{video_id}.{self._language}.{ext}")
            if os.path.exists(path):
                return path
        return None

    async def _emit_usage(
        self,
        video_id: str,
        operation: str,
        model: str,
        success: bool,
        error: str | None = None,
    ) -> None:
        """Emit a usage record for the transcript fetch attempt."""
        if self._usage_ledger is None:
            return
        try:
            from datetime import datetime

            record = UsageRecord(
                timestamp=datetime.now(UTC),
                provider="ytdlp",
                operation=operation,
                model=model,
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                cost_usd=0.0,
                cache_hit=False,
                video_id=video_id,
            )
            await self._usage_ledger.record_usage(record)
        except Exception:
            pass

    async def fetch_transcript(
        self,
        video_id: str,
        language: str = "en",
    ) -> TranscriptResult:
        """Fetch transcript for a single YouTube video."""
        url = f"https://www.youtube.com/watch?v={video_id}"
        retry_waits = self._retry_wait_seconds
        total_attempts = len(retry_waits) + 1

        last_error: str | None = None

        for attempt in range(1, total_attempts + 1):
            try:
                with YoutubeDL(self._get_ydl_opts()) as ydl:
                    ydl.download([url])

                if not self._subtitle_file_exists(url):
                    last_error = "No English subtitles are available for this video."
                    await self._emit_usage(
                        video_id=video_id,
                        operation="fetch_transcript",
                        model="ytdlp",
                        success=False,
                        error=last_error,
                    )
                    return TranscriptResult(
                        video_id=video_id,
                        language=language,
                        raw_srt="",
                        cleaned_text="",
                        content_hash="",
                        artifact_path="",
                        byte_size=0,
                    )

                subtitle_path = self._find_subtitle_file(video_id)
                if subtitle_path is None:
                    last_error = "Subtitle file not found after download."
                    await self._emit_usage(
                        video_id=video_id,
                        operation="fetch_transcript",
                        model="ytdlp",
                        success=False,
                        error=last_error,
                    )
                    return TranscriptResult(
                        video_id=video_id,
                        language=language,
                        raw_srt="",
                        cleaned_text="",
                        content_hash="",
                        artifact_path="",
                        byte_size=0,
                    )

                with open(subtitle_path, encoding="utf-8") as f:
                    raw_srt = f.read()

                content_hash = sha256_text(raw_srt)
                artifact_path = subtitle_path
                byte_size = os.path.getsize(subtitle_path)

                if self._artifact_store is not None:
                    try:
                        stored_path = await self._artifact_store.write_text(
                            f"transcripts/{video_id}.{language}.srt", raw_srt
                        )
                        artifact_path = stored_path
                    except Exception:
                        pass

                self._cooldown_until = 0.0

                await self._emit_usage(
                    video_id=video_id,
                    operation="fetch_transcript",
                    model="ytdlp",
                    success=True,
                )

                return TranscriptResult(
                    video_id=video_id,
                    language=language,
                    raw_srt=raw_srt,
                    cleaned_text=raw_srt,
                    content_hash=content_hash,
                    artifact_path=artifact_path,
                    byte_size=byte_size,
                )

            except Exception as error:
                detail = str(error)
                last_error = detail
                category = self._classify_failure(detail)

                if category == "members_only":
                    await self._emit_usage(
                        video_id=video_id,
                        operation="fetch_transcript",
                        model="ytdlp",
                        success=False,
                        error="Members-only video; authentication unavailable.",
                    )
                    return TranscriptResult(
                        video_id=video_id,
                        language=language,
                        raw_srt="",
                        cleaned_text="",
                        content_hash="",
                        artifact_path="",
                        byte_size=0,
                    )

                if category == "no_subtitles":
                    await self._emit_usage(
                        video_id=video_id,
                        operation="fetch_transcript",
                        model="ytdlp",
                        success=False,
                        error="No English subtitles are available for this video.",
                    )
                    return TranscriptResult(
                        video_id=video_id,
                        language=language,
                        raw_srt="",
                        cleaned_text="",
                        content_hash="",
                        artifact_path="",
                        byte_size=0,
                    )

                if category == "rate_limited":
                    self._cooldown_until = max(
                        self._cooldown_until,
                        time.time() + self._rate_limit_cooldown_seconds,
                    )
                    if attempt >= total_attempts:
                        await self._emit_usage(
                            video_id=video_id,
                            operation="fetch_transcript",
                            model="ytdlp",
                            success=False,
                            error="YouTube rate-limited subtitle downloads (retries exhausted).",
                        )
                        return TranscriptResult(
                            video_id=video_id,
                            language=language,
                            raw_srt="",
                            cleaned_text="",
                            content_hash="",
                            artifact_path="",
                            byte_size=0,
                        )
                    wait_time = retry_waits[attempt - 1] + random.uniform(
                        0, self._retry_jitter_seconds
                    )
                    time.sleep(wait_time)
                    continue

                await self._emit_usage(
                    video_id=video_id,
                    operation="fetch_transcript",
                    model="ytdlp",
                    success=False,
                    error=self._safe_diagnostic(detail),
                )
                return TranscriptResult(
                    video_id=video_id,
                    language=language,
                    raw_srt="",
                    cleaned_text="",
                    content_hash="",
                    artifact_path="",
                    byte_size=0,
                )

        await self._emit_usage(
            video_id=video_id,
            operation="fetch_transcript",
            model="ytdlp",
            success=False,
            error=last_error or "Retry exhausted",
        )
        return TranscriptResult(
            video_id=video_id,
            language=language,
            raw_srt="",
            cleaned_text="",
            content_hash="",
            artifact_path="",
            byte_size=0,
        )

    async def fetch_transcripts(
        self,
        video_ids: list[str],
        language: str = "en",
    ) -> list[TranscriptResult]:
        """Fetch transcripts for multiple videos sequentially with pacing."""
        results: list[TranscriptResult] = []
        last_video_end = 0.0

        for index, vid in enumerate(video_ids):
            if index > 0:
                await self._wait_until_next_video(last_video_end)
            result = await self.fetch_transcript(vid, language)
            results.append(result)
            last_video_end = time.time()

        return results

    async def _wait_until_next_video(self, last_video_end: float) -> None:
        """Enforce inter-video pacing and the pipeline-wide rate-limit cooldown."""
        now = time.time()
        inter_video_gate = last_video_end + max(
            self._delay_between_downloads, self._min_delay_between_videos
        )
        gate = max(inter_video_gate, self._cooldown_until)
        remaining = gate - now
        if remaining > 0:
            await asyncio_sleep(remaining)


async def asyncio_sleep(seconds: float) -> None:
    """Async sleep helper."""
    import asyncio

    await asyncio.sleep(seconds)
