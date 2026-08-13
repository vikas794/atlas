from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class TranscriptResult:
    video_id: str
    language: str
    raw_srt: str
    cleaned_text: str
    content_hash: str
    artifact_path: str
    byte_size: int


class TranscriptProviderPort(Protocol):
    async def fetch_transcript(
        self,
        video_id: str,
        language: str = "en",
    ) -> TranscriptResult:
        """Fetch transcript for a single video."""
        ...

    async def fetch_transcripts(
        self,
        video_ids: list[str],
        language: str = "en",
    ) -> list[TranscriptResult]:
        """Fetch transcripts for multiple videos."""
        ...
