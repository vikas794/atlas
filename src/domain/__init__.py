from __future__ import annotations

from src.domain.exceptions import CacheError, DomainError, ProviderError, StorageError
from src.domain.interfaces.assignment_generator import AssignmentGeneratorPort, AssignmentResult
from src.domain.interfaces.cache import CacheKey, CachePort
from src.domain.interfaces.insights_provider import InsightsProviderPort, InsightsResult
from src.domain.interfaces.quiz_generator import QuizContext, QuizGeneratorPort, QuizResult
from src.domain.interfaces.storage import (
    ArtifactStorePort,
    CacheEntry,
    RunRecord,
    RunRepositoryPort,
)
from src.domain.interfaces.summarizer import SummarizerPort, SummaryContext, SummaryResult
from src.domain.interfaces.transcript_provider import TranscriptProviderPort, TranscriptResult
from src.domain.interfaces.usage_ledger import (
    CacheAggregate,
    OperationAggregate,
    ProviderAggregate,
    TimeRange,
    UsageAggregate,
    UsageLedgerPort,
    UsageRecord,
)
from src.domain.models.assignment import Assignment, AssignmentMetadata
from src.domain.models.comparison import ComparisonRow, InsightsReport, Recommendations
from src.domain.models.quiz import PlaylistResult, VideoQuizResult
from src.domain.models.summary import Summary, SummaryData, SummaryStatus
from src.domain.models.transcript import Transcript, TranscriptContent, TranscriptStatus
from src.domain.models.video import SummaryRef, TranscriptRef, VideoId, VideoMetadata
from src.domain.services.cache_key_builder import CacheKeyBuilder
from src.domain.services.comparison_inference import ComparisonInferenceService
from src.domain.services.hash_computer import RunHashComputer

__all__ = [
    "Assignment",
    "AssignmentGeneratorPort",
    "AssignmentMetadata",
    "AssignmentResult",
    "ArtifactStorePort",
    "CacheAggregate",
    "CacheEntry",
    "CacheError",
    "CacheKey",
    "CacheKeyBuilder",
    "CachePort",
    "ComparisonInferenceService",
    "ComparisonRow",
    "DomainError",
    "InsightsProviderPort",
    "InsightsReport",
    "InsightsResult",
    "OperationAggregate",
    "PlaylistResult",
    "ProviderAggregate",
    "ProviderError",
    "QuizContext",
    "QuizGeneratorPort",
    "QuizResult",
    "Recommendations",
    "RunHashComputer",
    "RunRecord",
    "RunRepositoryPort",
    "StorageError",
    "Summary",
    "SummaryContext",
    "SummaryData",
    "SummaryRef",
    "SummaryResult",
    "SummaryStatus",
    "SummarizerPort",
    "TimeRange",
    "Transcript",
    "TranscriptContent",
    "TranscriptProviderPort",
    "TranscriptRef",
    "TranscriptResult",
    "TranscriptStatus",
    "UsageAggregate",
    "UsageLedgerPort",
    "UsageRecord",
    "VideoId",
    "VideoMetadata",
    "VideoQuizResult",
]
