from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class InsightsResult:
    video_id: str
    learning_outcome: str
    difficulty_level: str
    teaching_style: str
    practical_value: str
    content_depth: str
    target_audience: str
    key_differentiators: str
    time_investment_worth: str
    prerequisites: str
    follow_up_recommendations: str
    content_hash: str
    artifact_path: str
    prompt_version: str
    model: str


class InsightsProviderPort(Protocol):
    async def generate_insights(
        self,
        video_metadata: dict,
        summary_data: dict,
        prompt_version: str = "v1",
        model: str = "gpt-4o-mini",
    ) -> InsightsResult:
        """Generate AI-powered insights for a single video."""
        ...

    async def generate_insights_batch(
        self,
        video_metadata_list: list[dict],
        summary_data_list: list[dict],
        prompt_version: str = "v1",
        model: str = "gpt-4o-mini",
    ) -> list[InsightsResult]:
        """Generate insights for multiple videos."""
        ...
