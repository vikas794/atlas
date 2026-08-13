from __future__ import annotations

from .search import SearchInput, SearchOutput
from .transcripts import TranscriptGenerationInput, TranscriptGenerationOutput
from .summaries import SummaryGenerationInput, SummaryGenerationOutput
from .comparison import ComparisonGenerationInput, ComparisonGenerationOutput
from .assignments import AssignmentGenerationInput, AssignmentGenerationOutput
from .quiz import QuizGenerationInput, QuizGenerationOutput

__all__ = [
    "SearchInput",
    "SearchOutput",
    "TranscriptGenerationInput",
    "TranscriptGenerationOutput",
    "SummaryGenerationInput",
    "SummaryGenerationOutput",
    "ComparisonGenerationInput",
    "ComparisonGenerationOutput",
    "AssignmentGenerationInput",
    "AssignmentGenerationOutput",
    "QuizGenerationInput",
    "QuizGenerationOutput",
]