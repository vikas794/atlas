from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ComparisonRow:
    video_id: str
    title: str
    channel: str
    published: str
    recency: str
    difficulty: str
    teaching_style: str
    practical_value: str
    content_depth: str
    worth_time: str
    learning_outcome: str
    target_audience: str
    prerequisites: str
    key_differentiators: str
    tools_count: int
    key_technologies: list[str] = field(default_factory=list)
    complexity_score: float = 0.0
    summary_available: bool = False
    url: str = ""
    full_overview: str = ""
    insights: list[str] = field(default_factory=list)
    applications: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class InsightsReport:
    overall_statistics: str
    technology_analysis: str
    complexity_analysis: str
    top_videos_by_complexity: list[tuple[str, float, str]]
    channel_analysis: dict[str, int]


@dataclass(frozen=True)
class Recommendations:
    start_here: str | None = None
    beginner_friendly: list[str] = field(default_factory=list)
    high_practical_value: list[str] = field(default_factory=list)
    recent_content: list[str] = field(default_factory=list)
    learning_path: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not any(
            [
                self.start_here,
                self.beginner_friendly,
                self.high_practical_value,
                self.recent_content,
                self.learning_path,
            ]
        )
