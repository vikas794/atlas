from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from backend.schemas.runs import (
    AssignmentArtifact,
    ComparisonRow,
    SummaryArtifact,
    TranscriptArtifact,
    VideoResult,
)
from backend.storage.repository import RunRepository

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.append(str(SRC_ROOT))

from src.compare_youtube_outputs import YouTubeOutputComparator  # noqa: E402
from src.utils import clean_srt_content  # noqa: E402


def read_videos(repository: RunRepository, run_id: str) -> list[VideoResult]:
    return [VideoResult(**video) for video in repository.get_videos(run_id)]


def read_transcripts(repository: RunRepository, run_id: str) -> list[TranscriptArtifact]:
    video_index = {video.video_id: video for video in read_videos(repository, run_id)}
    records = {record["video_id"]: record for record in repository.get_transcripts(run_id)}
    artifacts: list[TranscriptArtifact] = []

    for video_id, video in video_index.items():
        record = records.get(video_id)
        path = Path(record["artifact_path"]) if record and record.get("artifact_path") else None
        usable = (
            record is not None
            and record["status"] == "succeeded"
            and path is not None
            and path.exists()
        )
        raw_srt = ""
        if usable:
            try:
                raw_srt = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                usable = False
                raw_srt = ""

        artifacts.append(
            TranscriptArtifact(
                video_id=video_id,
                title=video.title,
                channel=video.channel,
                language=record["language"] if record else "en",
                transcript_path=str(path) if usable else None,
                raw_srt=raw_srt,
                cleaned_text=clean_srt_content(raw_srt) if usable else "",
                available=usable,
            )
        )

    return artifacts


def read_summaries(repository: RunRepository, run_id: str) -> list[SummaryArtifact]:
    video_index = {video.video_id: video for video in read_videos(repository, run_id)}
    records = {record["video_id"]: record for record in repository.get_summaries(run_id)}
    artifacts: list[SummaryArtifact] = []

    for video_id, video in video_index.items():
        record = records.get(video_id)
        usable = record is not None and record["status"] == "succeeded"
        data: dict[str, Any] = {}
        if record and record.get("data"):
            try:
                data = json.loads(record["data"])
            except (TypeError, json.JSONDecodeError):
                data = {}
        path = Path(record["artifact_path"]) if record and record.get("artifact_path") else None
        available = usable and path is not None and path.exists()

        artifacts.append(
            SummaryArtifact(
                video_id=video_id,
                title=video.title,
                channel=video.channel,
                url=video.url,
                summary_path=str(path) if path else None,
                high_level_overview=data.get("high_level_overview", ""),
                technical_breakdown=data.get("technical_breakdown", []),
                insights=data.get("insights", []),
                applications=data.get("applications", []),
                limitations=data.get("limitations", []),
                available=available,
            )
        )

    return artifacts


def parse_assignment_file(path: Path) -> tuple[dict[str, Any], str]:
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---\n"):
        return {}, content

    _, frontmatter, body = content.split("---", 2)
    metadata = yaml.safe_load(frontmatter) or {}
    return metadata, body.strip()


def _slugify_assignment_title(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "section"


def _strip_heading_prefix(value: str) -> str:
    cleaned = re.sub(r"^[#\s]+", "", value).strip()
    cleaned = re.sub(r"^\d+[\).\s-]+", "", cleaned).strip()
    return cleaned


def _looks_like_task_heading(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith("task ") or lowered.startswith("exercise ") or lowered.startswith("advanced ")


def _parse_assignment_sections(markdown: str) -> list[dict[str, Any]]:
    section_pattern = re.compile(
        r"(?m)^(#{2,3}\s+.+?|(?:Task|Exercise)\s+\d+[^\n]*|Advanced\s+\d+[^\n]*)$"
    )
    matches = list(section_pattern.finditer(markdown))
    if not matches:
        return []

    sections: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        heading = match.group(0).strip()
        body = markdown[match.end() : end].strip()
        title = _strip_heading_prefix(heading)
        kind = "task" if _looks_like_task_heading(title) else "section"
        sections.append(
            {
                "id": _slugify_assignment_title(title),
                "title": title,
                "kind": kind,
                "markdown": body,
            }
        )

    return sections


def _parse_assignment_checklist(markdown: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for line in markdown.splitlines():
        match = re.match(r"^\s*-\s*\[(?: |x|X)\]\s+(.*)$", line)
        if not match:
            continue
        label = match.group(1).strip()
        items.append({"id": _slugify_assignment_title(label), "label": label})
    return items


def _build_assignment_display_metadata(metadata: dict[str, Any]) -> dict[str, str]:
    allowed_keys = (
        "difficulty_level",
        "model_used",
        "channel",
        "video_id",
        "video_title",
        "video_url",
    )
    return {
        key: str(value)
        for key, value in metadata.items()
        if key in allowed_keys and value not in (None, "")
    }


def read_assignments(repository: RunRepository, run_id: str) -> list[AssignmentArtifact]:
    video_index = {video.video_id: video for video in read_videos(repository, run_id)}
    records = {record["video_id"]: record for record in repository.get_assignments(run_id)}
    artifacts: list[AssignmentArtifact] = []

    for video_id, video in video_index.items():
        record = records.get(video_id)
        path = Path(record["artifact_path"]) if record and record.get("artifact_path") else None
        usable = (
            record is not None
            and record["status"] == "succeeded"
            and path is not None
            and path.exists()
        )
        metadata: dict[str, Any] = {}
        if record and record.get("metadata"):
            try:
                metadata = json.loads(record["metadata"])
            except (TypeError, json.JSONDecodeError):
                metadata = {}
        markdown = ""
        sections: list[dict[str, Any]] = []
        checklist: list[dict[str, str]] = []
        if usable:
            try:
                _, markdown = parse_assignment_file(path)
                sections = _parse_assignment_sections(markdown)
                checklist = _parse_assignment_checklist(markdown)
            except Exception:
                markdown, sections, checklist = "", [], []

        artifacts.append(
            AssignmentArtifact(
                video_id=video_id,
                title=video.title,
                channel=video.channel,
                url=video.url,
                assignment_path=str(path) if usable else None,
                metadata=metadata,
                display_metadata=_build_assignment_display_metadata(metadata),
                markdown=markdown,
                sections=sections,
                checklist=checklist,
                available=usable,
            )
        )

    return artifacts


def _normalize_sentence(text: str, max_length: int = 220) -> str:
    value = re.sub(r"\s+", " ", text).strip()
    if len(value) <= max_length:
        return value
    return value[: max_length - 3].rstrip() + "..."


def _infer_recency(published_at: str) -> tuple[str, str]:
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


def _infer_difficulty(title: str, overview: str, complexity_score: float) -> str:
    title_lower = title.lower()
    overview_lower = overview.lower()
    if "advanced" in title_lower or "advanced" in overview_lower:
        return "Advanced"
    if complexity_score >= 70:
        return "Advanced"
    if complexity_score >= 30:
        return "Intermediate"
    return "Beginner"


def _infer_teaching_style(title: str, overview: str, applications: list[str]) -> str:
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


def _infer_practical_value(overview: str, applications: list[str]) -> str:
    combined = " ".join([overview, *applications]).lower()
    if "hands-on" in combined or "practical" in combined or "build" in combined or len(applications) >= 3:
        return "High"
    if applications:
        return "Medium"
    return "Low"


def _infer_content_depth(complexity_score: float, process_count: int, overview_length: int) -> str:
    if complexity_score >= 80 or process_count >= 14 or overview_length >= 900:
        return "Deep-dive"
    if complexity_score >= 30 or process_count >= 6 or overview_length >= 350:
        return "Moderate"
    return "Surface-level"


def _infer_target_audience(title: str, overview: str, key_technologies: list[str]) -> str:
    combined = " ".join([title, overview, " ".join(key_technologies)]).lower()
    if "ml engineer" in combined or "machine learning" in combined:
        return "Software engineers and ML engineers"
    if "python" in combined:
        return "Python engineers and AI builders"
    if "engineer" in combined or "developer" in combined:
        return "Software engineers and developers"
    return "Technical learners"


def _infer_prerequisites(title: str, overview: str, key_technologies: list[str]) -> str:
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


def _infer_learning_outcome(overview: str, insights: list[str]) -> str:
    if insights:
        return _normalize_sentence(insights[0], max_length=180)
    return _normalize_sentence(overview, max_length=180)


def _infer_key_differentiators(overview: str, applications: list[str]) -> str:
    if applications:
        return _normalize_sentence(applications[0], max_length=160)
    return _normalize_sentence(overview, max_length=160)


def _infer_worth_time(practical_value: str, content_depth: str) -> str:
    if practical_value == "High" and content_depth in {"Moderate", "Deep-dive"}:
        return "Yes"
    if practical_value == "Low":
        return "No"
    return "Maybe"


def build_comparison_artifact(
    repository: RunRepository, run_id: str
) -> tuple[list[ComparisonRow], str, list[str]]:
    comparator = YouTubeOutputComparator(
        repository=repository,
        run_id=run_id,
        use_ai_insights=False,
        num_workers=0,
    )
    video_metadata = comparator.load_video_metadata()
    summary_data = comparator.load_summary_data()
    insights_report = comparator.generate_insights_report(video_metadata, summary_data)

    rows: list[ComparisonRow] = []
    for video_id, video_meta in video_metadata.items():
        summary = summary_data.get(video_id, {})
        extracted = comparator.extract_key_insights(summary) if summary else {}
        key_technologies = extracted.get("tools_mentioned", [])

        published, recency = _infer_recency(video_meta.get("published_at", ""))
        difficulty = _infer_difficulty(
            video_meta.get("title", ""),
            summary.get("high_level_overview", ""),
            extracted.get("complexity_score", 0),
        )
        teaching_style = _infer_teaching_style(
            video_meta.get("title", ""),
            summary.get("high_level_overview", ""),
            summary.get("applications", []),
        )
        practical_value = _infer_practical_value(
            summary.get("high_level_overview", ""),
            summary.get("applications", []),
        )
        content_depth = _infer_content_depth(
            extracted.get("complexity_score", 0),
            extracted.get("num_processes", 0),
            len(summary.get("high_level_overview", "")),
        )

        rows.append(
            ComparisonRow(
                video_id=video_id,
                title=video_meta.get("title", "Unknown Title"),
                channel=video_meta.get("channel", "Unknown Channel"),
                published=published,
                recency=recency,
                difficulty=difficulty,
                teaching_style=teaching_style,
                practical_value=practical_value,
                content_depth=content_depth,
                worth_time=_infer_worth_time(practical_value, content_depth),
                learning_outcome=_infer_learning_outcome(
                    summary.get("high_level_overview", ""),
                    summary.get("insights", []),
                ),
                target_audience=_infer_target_audience(
                    video_meta.get("title", ""),
                    summary.get("high_level_overview", ""),
                    key_technologies,
                ),
                prerequisites=_infer_prerequisites(
                    video_meta.get("title", ""),
                    summary.get("high_level_overview", ""),
                    key_technologies,
                ),
                key_differentiators=_infer_key_differentiators(
                    summary.get("high_level_overview", ""),
                    summary.get("applications", []),
                ),
                tools_count=extracted.get("num_tools", 0),
                key_technologies=key_technologies,
                complexity_score=round(extracted.get("complexity_score", 0), 1),
                summary_available=bool(summary),
                url=video_meta.get("url", ""),
                full_overview=summary.get("high_level_overview", ""),
                insights=summary.get("insights", []),
                applications=summary.get("applications", []),
                limitations=summary.get("limitations", []),
            )
        )

    rows.sort(
        key=lambda row: (
            {"High": 3, "Medium": 2, "Low": 1}.get(row.practical_value, 0),
            {"Yes": 3, "Maybe": 2, "No": 1}.get(row.worth_time, 0),
            row.complexity_score,
        ),
        reverse=True,
    )

    recommendations = [
        f"Start with '{rows[0].title}' for the strongest practical payoff." if rows else "",
        f"Use '{rows[0].title}' as the anchor lesson, then compare with '{rows[1].title}' for contrast."
        if len(rows) > 1
        else "",
        "Switch to a live comparison refresh only when you want fresh AI-derived teaching-style labels.",
    ]

    return rows, insights_report, [item for item in recommendations if item]


def build_run_counts(repository: RunRepository, run_id: str) -> dict[str, int]:
    videos = len(read_videos(repository, run_id))
    transcripts = len([item for item in read_transcripts(repository, run_id) if item.available])
    summaries = len([item for item in read_summaries(repository, run_id) if item.available])
    assignments = len([item for item in read_assignments(repository, run_id) if item.available])
    return {
        "videos": videos,
        "transcripts": transcripts,
        "summaries": summaries,
        "assignments": assignments,
    }


def has_comparison_source_data(repository: RunRepository, run_id: str) -> bool:
    counts = build_run_counts(repository, run_id)
    return counts["videos"] > 0 and counts["summaries"] > 0
