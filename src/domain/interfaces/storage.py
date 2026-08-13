from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.domain.models.assignment import Assignment
from src.domain.models.comparison import ComparisonRow, InsightsReport, Recommendations
from src.domain.models.quiz import PlaylistResult
from src.domain.models.summary import Summary
from src.domain.models.transcript import Transcript
from src.domain.models.video import VideoMetadata


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    cache_key: str | None
    search_query: str
    normalized_query: str
    max_videos: int
    transcript_language: str
    status: str
    created_at: str
    updated_at: str
    source_folder: str
    is_fallback: bool
    videos_hash: str | None = None
    transcripts_hash: str | None = None
    summaries_hash: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class CacheEntry:
    cache_key: str
    kind: str
    run_id: str
    normalized_query: str
    settings_hash: str
    status: str
    created_at: str
    expires_at: str
    hit_count: int = 0
    last_hit_at: str | None = None


class RunRepositoryPort(Protocol):
    # Run lifecycle
    async def create_run(
        self,
        run_id: str,
        cache_key: str | None,
        search_query: str,
        normalized_query: str,
        max_videos: int,
        transcript_language: str,
        is_fallback: bool,
    ) -> str:
        ...

    async def get_run(self, run_id: str) -> RunRecord | None:
        ...

    async def list_runs(self) -> list[RunRecord]:
        ...

    async def latest_run(self) -> RunRecord | None:
        ...

    async def set_run_status(self, run_id: str, status: str, error: str | None = None) -> None:
        ...

    # Videos
    async def set_videos(self, run_id: str, videos: list[VideoMetadata]) -> None:
        ...

    async def get_videos(self, run_id: str) -> list[VideoMetadata]:
        ...

    async def videos_state_hash(self, run_id: str) -> str:
        ...

    # Transcripts
    async def upsert_transcripts(
        self,
        run_id: str,
        transcripts: list[Transcript],
        settings: dict | None = None,
    ) -> None:
        ...

    async def get_transcripts(self, run_id: str) -> list[Transcript]:
        ...

    async def transcripts_state_hash(self, run_id: str) -> str:
        ...

    # Summaries
    async def upsert_summaries(
        self,
        run_id: str,
        summaries: list[Summary],
        settings: dict | None = None,
    ) -> None:
        ...

    async def get_summaries(self, run_id: str) -> list[Summary]:
        ...

    async def summaries_state_hash(self, run_id: str) -> str:
        ...

    # Comparisons
    async def set_comparison(
        self,
        run_id: str,
        rows: list[ComparisonRow],
        insights_report: InsightsReport,
        recommendations: Recommendations,
        settings: dict | None = None,
        status: str = "succeeded",
        error: str | None = None,
    ) -> None:
        ...

    async def get_comparison(self, run_id: str) -> tuple[list[ComparisonRow], InsightsReport, Recommendations] | None:
        ...

    # Assignments
    async def upsert_assignments(
        self,
        run_id: str,
        assignments: list[Assignment],
        settings: dict | None = None,
    ) -> None:
        ...

    async def get_assignments(self, run_id: str) -> list[Assignment]:
        ...

    # Quiz
    async def set_quiz_result(
        self,
        run_id: str,
        result: PlaylistResult,
        settings: dict | None = None,
    ) -> None:
        ...

    async def get_quiz_result(self, run_id: str) -> PlaylistResult | None:
        ...

    # Hash recomputation
    async def recompute_run_hashes(self, run_id: str) -> None:
        ...

    async def mark_stale_derived(self, run_id: str) -> None:
        ...

    # Maintenance
    async def purge_expired(self, retention_days: int = 90) -> dict:
        ...

    async def stats(self) -> dict:
        ...


class ArtifactStorePort(Protocol):
    async def write_text(self, path: str, content: str) -> str:
        """Write text content and return content hash."""
        ...

    async def write_bytes(self, path: str, content: bytes) -> str:
        """Write binary content and return content hash."""
        ...

    async def read_text(self, path: str) -> str | None:
        """Read text content from path."""
        ...

    async def read_bytes(self, path: str) -> bytes | None:
        """Read binary content from path."""
        ...

    async def delete(self, path: str) -> None:
        """Delete artifact at path."""
        ...

    async def exists(self, path: str) -> bool:
        """Check if artifact exists."""
        ...

    async def file_size(self, path: str) -> int | None:
        """Get file size in bytes."""
        ...
