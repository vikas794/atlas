from __future__ import annotations

import re
from datetime import datetime


class ComparisonInferenceService:
    """Pure domain logic for inferring comparison attributes from video/summary data.

    All methods are stateless and side-effect free. Logic is copied from
    backend/services/artifact_readers.py:_infer_* functions.
    """

    @staticmethod
    def _normalize_sentence(text: str, max_length: int = 220) -> str:
        value = re.sub(r"\s+", " ", text).strip()
        if len(value) <= max_length:
            return value
        return value[: max_length - 3].rstrip() + "..."

    @staticmethod
    def infer_recency(published_at: str) -> tuple[str, str]:
        """Return (formatted_date, recency_label)."""
        if not published_at:
            return "N/A", "Unknown"

        try:
            published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            formatted = published.strftime("%Y-%m-%d")
            age_days = (datetime.now() - published.replace(tzinfo=None)).days
        except ValueError:
            return published_at[:10], "Unknown"

        if age_days < 30:
            recency = "Very Recent"
        elif age_days < 180:
            recency = "Recent"
        else:
            recency = "Older"

        return formatted, recency

    @staticmethod
    def infer_difficulty(title: str, overview: str, complexity_score: float) -> str:
        title_lower = title.lower()
        overview_lower = overview.lower()
        if "advanced" in title_lower or "advanced" in overview_lower:
            return "Advanced"
        if complexity_score >= 70:
            return "Advanced"
        if complexity_score >= 30:
            return "Intermediate"
        return "Beginner"

    @staticmethod
    def infer_teaching_style(title: str, overview: str, applications: list[str]) -> str:
        combined = " ".join([title, overview, *applications]).lower()
        if "code-along" in combined or "code along" in combined:
            return "Code-along"
        if "project" in combined or "end-to-end" in combined or "end to end" in combined:
            return "Project-based"
        if "theory" in combined:
            return "Theory-focused"
        if "explains" in combined or "explanation" in combined:
            return "Explanation-heavy"
        return "Mixed"

    @staticmethod
    def infer_practical_value(overview: str, applications: list[str]) -> str:
        combined = " ".join([overview, *applications]).lower()
        if "hands-on" in combined or "practical" in combined or "build" in combined or len(applications) >= 3:
            return "High"
        if applications:
            return "Medium"
        return "Low"

    @staticmethod
    def infer_content_depth(complexity_score: float, process_count: int, overview_length: int) -> str:
        if complexity_score >= 80 or process_count >= 14 or overview_length >= 900:
            return "Deep-dive"
        if complexity_score >= 30 or process_count >= 6 or overview_length >= 350:
            return "Moderate"
        return "Surface-level"

    @staticmethod
    def infer_target_audience(title: str, overview: str, key_technologies: list[str]) -> str:
        combined = " ".join([title, overview, " ".join(key_technologies)]).lower()
        if "ml engineer" in combined or "machine learning" in combined:
            return "Software engineers and ML engineers"
        if "python" in combined:
            return "Python engineers and AI builders"
        if "engineer" in combined or "developer" in combined:
            return "Software engineers and developers"
        return "Technical learners"

    @staticmethod
    def infer_prerequisites(title: str, overview: str, key_technologies: list[str]) -> str:
        combined = " ".join([title, overview, " ".join(key_technologies)]).lower()
        prerequisites: list[str] = []
        if "python" in combined:
            prerequisites.append("Comfortable with Python")
        if "api" in combined:
            prerequisites.append("Basic API usage")
        if "rag" in combined or "vector" in combined:
            prerequisites.append("Familiar with retrieval workflows")
        if "agent" in combined or "llm" in combined:
            prerequisites.append("Basic LLM and agent concepts")
        if not prerequisites:
            prerequisites.append("Some programming experience")
        return ", ".join(prerequisites)

    @staticmethod
    def infer_learning_outcome(overview: str, insights: list[str]) -> str:
        if insights:
            return ComparisonInferenceService._normalize_sentence(insights[0], max_length=180)
        return ComparisonInferenceService._normalize_sentence(overview, max_length=180)

    @staticmethod
    def infer_key_differentiators(overview: str, applications: list[str]) -> str:
        if applications:
            return ComparisonInferenceService._normalize_sentence(applications[0], max_length=160)
        return ComparisonInferenceService._normalize_sentence(overview, max_length=160)

    @staticmethod
    def infer_worth_time(practical_value: str, content_depth: str) -> str:
        if practical_value == "High" and content_depth in {"Moderate", "Deep-dive"}:
            return "Yes"
        if practical_value == "Low":
            return "No"
        return "Maybe"
