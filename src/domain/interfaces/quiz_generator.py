from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.domain.models.quiz import PlaylistResult


@dataclass(frozen=True)
class QuizContext:
    playlist_url: str
    gemini_api_key: str | None = None
    max_videos: int | None = None
    model: str = "gemini-1.5-pro"


@dataclass(frozen=True)
class QuizResult:
    playlist_result: PlaylistResult
    drive_folder_url: str | None = None


class QuizGeneratorPort(Protocol):
    async def generate_quiz(
        self,
        context: QuizContext,
    ) -> QuizResult:
        """Generate a quiz from a YouTube playlist."""
        ...
