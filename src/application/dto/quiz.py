from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Optional


@dataclass(frozen=True)
class VideoQuizResult:
    position: int
    video_id: str
    title: str
    status: str
    doc_url: Optional[str] = None


@dataclass(frozen=True)
class QuizGenerationInput:
    playlist_url: str
    gemini_api_key: Optional[str] = None
    use_env_keys: bool = True
    max_videos: Optional[int] = None
    progress_callback: Optional[Callable[[dict[str, Any]], None]] = None


@dataclass(frozen=True)
class QuizGenerationOutput:
    status: str
    playlist_title: str
    total_videos: int
    processed: int
    failed: int
    drive_folder_url: Optional[str] = None
    video_results: List[VideoQuizResult] = None

    def __post_init__(self) -> None:
        if self.video_results is None:
            object.__setattr__(self, "video_results", [])