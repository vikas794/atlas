from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class TranscriptStatus(Enum):
    CREATED = "created"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STALE = "stale"


@dataclass(frozen=True)
class TranscriptContent:
    raw_srt: str = ""
    cleaned_text: str = ""

    def is_empty(self) -> bool:
        return not self.raw_srt and not self.cleaned_text


@dataclass(frozen=True)
class Transcript:
    video_id: str
    title: str
    channel: str
    language: str = "en"
    content: TranscriptContent = field(default_factory=TranscriptContent)
    artifact_path: str | None = None
    content_hash: str | None = None
    status: TranscriptStatus = TranscriptStatus.CREATED
    available: bool = False

    def mark_available(self) -> Transcript:
        return Transcript(
            video_id=self.video_id,
            title=self.title,
            channel=self.channel,
            language=self.language,
            content=self.content,
            artifact_path=self.artifact_path,
            content_hash=self.content_hash,
            status=TranscriptStatus.SUCCEEDED,
            available=True,
        )

    def mark_failed(self, error: str = "") -> Transcript:
        return Transcript(
            video_id=self.video_id,
            title=self.title,
            channel=self.channel,
            language=self.language,
            content=self.content,
            artifact_path=self.artifact_path,
            content_hash=self.content_hash,
            status=TranscriptStatus.FAILED,
            available=False,
        )
