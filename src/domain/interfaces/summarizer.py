from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SummaryContext:
    video_id: str
    title: str
    channel: str
    transcript_text: str
    language: str = "en"
    prompt_version: str = "v1"
    model: str = "gpt-4o-mini"


@dataclass(frozen=True)
class SummaryResult:
    video_id: str
    summary_data: dict
    content_hash: str
    artifact_path: str
    byte_size: int
    prompt_version: str
    model: str


class SummarizerPort(Protocol):
    async def summarize(
        self,
        context: SummaryContext,
    ) -> SummaryResult:
        """Generate a summary from transcript text."""
        ...

    async def summarize_batch(
        self,
        contexts: list[SummaryContext],
    ) -> list[SummaryResult]:
        """Generate summaries for multiple transcripts."""
        ...
