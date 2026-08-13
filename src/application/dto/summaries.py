from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SummaryGenerationInput:
    run_id: str
    refresh: bool = False
    num_workers: Optional[int] = None
    transcript_language: str = "en"


@dataclass(frozen=True)
class SummaryGenerationOutput:
    run_id: str
    source_folder: str
    status: str
    detail: str