from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SummaryStatus(Enum):
    CREATED = "created"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STALE = "stale"


@dataclass(frozen=True)
class SummaryData:
    high_level_overview: str = ""
    technical_breakdown: list[dict] = field(default_factory=list)
    insights: list[str] = field(default_factory=list)
    applications: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return (
            not self.high_level_overview
            and not self.technical_breakdown
            and not self.insights
            and not self.applications
            and not self.limitations
        )


@dataclass(frozen=True)
class Summary:
    video_id: str
    title: str
    channel: str
    url: str = ""
    data: SummaryData = field(default_factory=SummaryData)
    artifact_path: str | None = None
    content_hash: str | None = None
    status: SummaryStatus = SummaryStatus.CREATED
    available: bool = False

    def mark_available(self) -> Summary:
        return Summary(
            video_id=self.video_id,
            title=self.title,
            channel=self.channel,
            url=self.url,
            data=self.data,
            artifact_path=self.artifact_path,
            content_hash=self.content_hash,
            status=SummaryStatus.SUCCEEDED,
            available=True,
        )

    def mark_failed(self, error: str = "") -> Summary:
        return Summary(
            video_id=self.video_id,
            title=self.title,
            channel=self.channel,
            url=self.url,
            data=self.data,
            artifact_path=self.artifact_path,
            content_hash=self.content_hash,
            status=SummaryStatus.FAILED,
            available=False,
        )
