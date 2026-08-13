from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TranscriptGenerationInput:
    run_id: str
    refresh: bool = False
    num_workers: Optional[int] = None
    transcript_language: str = "en"


@dataclass(frozen=True)
class TranscriptGenerationOutput:
    run_id: str
    source_folder: str
    status: str
    detail: str
    transcripts_available: Optional[int] = None