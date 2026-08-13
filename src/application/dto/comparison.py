from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ComparisonGenerationInput:
    run_id: str
    refresh: bool = False
    num_workers: Optional[int] = None
    use_ai_insights: bool = False


@dataclass(frozen=True)
class ComparisonGenerationOutput:
    run_id: str
    source_folder: str
    status: str
    detail: str