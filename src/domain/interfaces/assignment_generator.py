from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AssignmentResult:
    video_id: str
    markdown: str
    sections: list[dict]
    checklist: list[dict]
    metadata: dict
    display_metadata: dict[str, str]
    content_hash: str
    artifact_path: str
    byte_size: int
    prompt_version: str
    model: str


class AssignmentGeneratorPort(Protocol):
    async def generate_assignment(
        self,
        video_id: str,
        title: str,
        channel: str,
        summary_data: dict,
        prompt_version: str = "v1",
        model: str = "gpt-4o-mini",
    ) -> AssignmentResult:
        """Generate an assignment from summary data."""
        ...

    async def generate_assignments_batch(
        self,
        videos: list[dict],
        summaries: list[dict],
        prompt_version: str = "v1",
        model: str = "gpt-4o-mini",
    ) -> list[AssignmentResult]:
        """Generate assignments for multiple videos."""
        ...
