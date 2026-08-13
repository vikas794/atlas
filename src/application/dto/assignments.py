from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AssignmentGenerationInput:
    run_id: str
    refresh: bool = False
    num_workers: Optional[int] = None


@dataclass(frozen=True)
class AssignmentGenerationOutput:
    run_id: str
    source_folder: str
    status: str
    detail: str