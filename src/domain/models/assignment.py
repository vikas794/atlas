from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AssignmentMetadata:
    difficulty_level: str = ""
    model_used: str = ""
    channel: str = ""
    video_id: str = ""
    video_title: str = ""
    video_url: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> AssignmentMetadata:
        allowed = {
            "difficulty_level",
            "model_used",
            "channel",
            "video_id",
            "video_title",
            "video_url",
        }
        extra = {k: v for k, v in data.items() if k not in allowed}
        return cls(
            difficulty_level=data.get("difficulty_level", ""),
            model_used=data.get("model_used", ""),
            channel=data.get("channel", ""),
            video_id=data.get("video_id", ""),
            video_title=data.get("video_title", ""),
            video_url=data.get("video_url", ""),
            extra=extra,
        )


@dataclass(frozen=True)
class Assignment:
    video_id: str
    title: str
    channel: str
    url: str = ""
    markdown: str = ""
    sections: list[dict] = field(default_factory=list)
    checklist: list[dict] = field(default_factory=list)
    metadata: AssignmentMetadata = field(default_factory=AssignmentMetadata)
    artifact_path: str | None = None
    content_hash: str | None = None
    available: bool = False

    def mark_available(self) -> Assignment:
        return Assignment(
            video_id=self.video_id,
            title=self.title,
            channel=self.channel,
            url=self.url,
            markdown=self.markdown,
            sections=self.sections,
            checklist=self.checklist,
            metadata=self.metadata,
            artifact_path=self.artifact_path,
            content_hash=self.content_hash,
            available=True,
        )
