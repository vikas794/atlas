from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SearchInput:
    query: str
    max_videos: int = 4
    transcript_language: str = "en"
    num_workers: int = 4
    use_env_keys: bool = True
    openrouter_api_key: Optional[str] = None
    youtube_api_key: Optional[str] = None
    prefer_cache: bool = True


@dataclass(frozen=True)
class SearchOutput:
    run_id: str
    source_folder: str
    status: str
    detail: str
    transcripts_available: Optional[int] = None