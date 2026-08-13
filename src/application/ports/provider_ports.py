from __future__ import annotations

from typing import TYPE_CHECKING

from src.domain.interfaces.cache import CachePort, CacheKey
from src.domain.interfaces.storage import (
    RunRepositoryPort,
    ArtifactStorePort,
    RunRecord,
    CacheEntry,
)
from src.domain.interfaces.transcript_provider import TranscriptProviderPort, TranscriptResult
from src.domain.interfaces.summarizer import SummarizerPort, SummaryContext, SummaryResult
from src.domain.interfaces.insights_provider import InsightsProviderPort, InsightsResult
from src.domain.interfaces.assignment_generator import AssignmentGeneratorPort, AssignmentResult
from src.domain.interfaces.quiz_generator import QuizGeneratorPort, QuizContext, QuizResult
from src.domain.interfaces.usage_ledger import (
    UsageLedgerPort,
    UsageRecord,
    UsageAggregate,
    ProviderAggregate,
    OperationAggregate,
    CacheAggregate,
    TimeRange,
)

if TYPE_CHECKING:
    from src.domain.models.video import VideoMetadata
    from src.domain.models.transcript import Transcript
    from src.domain.models.summary import Summary
    from src.domain.models.comparison import ComparisonRow, InsightsReport, Recommendations
    from src.domain.models.assignment import Assignment
    from src.domain.models.quiz import PlaylistResult


RunRepositoryPort.__doc__ = (
    "Application-level run repository port. "
    "Delegates to domain RunRepositoryPort with dict-based records matching "
    "SqlRunRepository implementation."
)

__all__ = [
    "RunRepositoryPort",
    "TranscriptProviderPort",
    "SummarizerPort",
    "InsightsProviderPort",
    "AssignmentGeneratorPort",
    "QuizGeneratorPort",
    "CachePort",
    "UsageLedgerPort",
    "ArtifactStorePort",
    "CacheKey",
    "RunRecord",
    "CacheEntry",
    "TranscriptResult",
    "SummaryContext",
    "SummaryResult",
    "InsightsResult",
    "AssignmentResult",
    "QuizContext",
    "QuizResult",
    "UsageRecord",
    "UsageAggregate",
    "ProviderAggregate",
    "OperationAggregate",
    "CacheAggregate",
    "TimeRange",
]