from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class VideoId:
    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("VideoId cannot be empty")


@dataclass(frozen=True)
class VideoMetadata:
    video_id: VideoId
    title: str
    channel: str
    url: str
    description: str = ""
    published_at: str = ""
    duration: str = "Unknown"

    @property
    def published_datetime(self) -> datetime | None:
        if not self.published_at:
            return None
        try:
            return datetime.fromisoformat(self.published_at.replace("Z", "+00:00"))
        except ValueError:
            return None


@dataclass(frozen=True)
class TranscriptRef:
    video_id: VideoId
    language: str
    content_hash: str | None = None
    artifact_path: str | None = None
    available: bool = False


@dataclass(frozen=True)
class SummaryRef:
    video_id: VideoId
    content_hash: str | None = None
    artifact_path: str | None = None
    available: bool = False
