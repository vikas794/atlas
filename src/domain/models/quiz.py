from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class VideoQuizResult:
    position: int
    video_id: str
    title: str
    status: str
    doc_url: str | None = None


@dataclass(frozen=True)
class PlaylistResult:
    playlist_title: str
    status: str
    total_videos: int
    processed: int
    failed: int
    drive_folder_url: str | None = None
    video_results: list[VideoQuizResult] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        if self.total_videos == 0:
            return 0.0
        return self.processed / self.total_videos * 100
