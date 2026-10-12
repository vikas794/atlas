"""Domain model for the playlist-to-notes pipeline.

Covers the four entities in PRD section 7: playlists, videos, transcripts and
summaries, plus the stage enum and the failure taxonomy from PRD section 8.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Stage(StrEnum):
    """Per-video pipeline stage.

    The happy path is ordered; `FAILED` is reachable from any active stage and
    returns to `QUEUED` only through a retry. See PRD section 15.
    """

    QUEUED = "queued"
    FETCHING_TRANSCRIPT = "fetching_transcript"
    TRANSCRIPT_READY = "transcript_ready"
    GENERATING_NOTES = "generating_notes"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    PUBLISHED = "published"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        """True for stages that require no further pipeline work."""
        return self in _TERMINAL_STAGES


_TERMINAL_STAGES = frozenset({Stage.PUBLISHED})


class FailureKind(StrEnum):
    """Whether a failure is worth retrying. PRD section 8.

    This is deliberately separate from `FailureType`: the type names *what* went
    wrong, the kind decides *whether to try again*.
    """

    TRANSIENT = "transient"
    PERMANENT = "permanent"


class FailureType(StrEnum):
    """Specific, human-readable failure reason codes. PRD section 8."""

    # Permanent — video cannot be fetched by any retry.
    VIDEO_PRIVATE = "video_private"
    VIDEO_DELETED = "video_deleted"
    AGE_RESTRICTED = "age_restricted"
    REGION_BLOCKED = "region_blocked"
    MEMBERS_ONLY = "members_only"
    TRANSCRIPTS_DISABLED = "transcripts_disabled"
    NO_USABLE_LANGUAGE = "no_usable_language"
    TRANSCRIPT_TOO_LARGE = "transcript_too_large"

    # Transient — retry with exponential backoff.
    RATE_LIMITED = "rate_limited"
    NETWORK_ERROR = "network_error"
    PROVIDER_ERROR = "provider_error"
    OUTPUT_MALFORMED = "output_malformed"

    # Atlas-side problems: never worth retrying.
    CONFIG_ERROR = "config_error"
    INTERNAL_ERROR = "internal_error"

    # Non-video-specific.
    PLAYLIST_UNAVAILABLE = "playlist_unavailable"
    INVALID_URL = "invalid_url"
    DRIVE_ERROR = "drive_error"


#: Every failure type and whether the pipeline may retry it automatically.
FAILURE_KINDS: dict[FailureType, FailureKind] = {
    FailureType.VIDEO_PRIVATE: FailureKind.PERMANENT,
    FailureType.VIDEO_DELETED: FailureKind.PERMANENT,
    FailureType.AGE_RESTRICTED: FailureKind.PERMANENT,
    FailureType.REGION_BLOCKED: FailureKind.PERMANENT,
    FailureType.MEMBERS_ONLY: FailureKind.PERMANENT,
    FailureType.TRANSCRIPTS_DISABLED: FailureKind.PERMANENT,
    FailureType.NO_USABLE_LANGUAGE: FailureKind.PERMANENT,
    FailureType.TRANSCRIPT_TOO_LARGE: FailureKind.PERMANENT,
    FailureType.RATE_LIMITED: FailureKind.TRANSIENT,
    FailureType.NETWORK_ERROR: FailureKind.TRANSIENT,
    FailureType.PROVIDER_ERROR: FailureKind.TRANSIENT,
    FailureType.OUTPUT_MALFORMED: FailureKind.TRANSIENT,
    FailureType.CONFIG_ERROR: FailureKind.PERMANENT,
    FailureType.INTERNAL_ERROR: FailureKind.PERMANENT,
    FailureType.PLAYLIST_UNAVAILABLE: FailureKind.PERMANENT,
    FailureType.INVALID_URL: FailureKind.PERMANENT,
    FailureType.DRIVE_ERROR: FailureKind.TRANSIENT,
}


def kind_of(failure_type: FailureType) -> FailureKind:
    """Return whether a failure type should be retried automatically."""
    return FAILURE_KINDS.get(failure_type, FailureKind.PERMANENT)


class PipelineError(Exception):
    """Base for pipeline errors that carry a classified failure.

    Every error the worker must triage is one of these, so the retry policy
    never has to inspect raw provider strings.
    """

    def __init__(
        self,
        failure_type: FailureType,
        reason: str,
        *,
        diagnostic: str | None = None,
    ) -> None:
        super().__init__(reason)
        self.failure_type = failure_type
        self.failure_kind = kind_of(failure_type)
        self.reason = reason
        #: Log-safe, already truncated. Never contains keys or transcript text.
        self.diagnostic = diagnostic

    def __str__(self) -> str:
        return f"[{self.failure_type.value}/{self.failure_kind.value}] {self.reason}"


class TranscriptError(PipelineError):
    """Raised when a video's transcript cannot be produced."""


class NotesGenerationError(PipelineError):
    """Raised when the LLM cannot produce usable notes for a video."""


@dataclass
class Playlist:
    """A tracked YouTube playlist."""

    playlist_id: str
    source_url: str
    title: str = ""
    id: int | None = None
    video_count: int = 0
    #: ``ingested`` | ``publishing`` | ``published`` | ``publish_failed`` (PRD section 16:
    #: publishing runs in the background worker, so the UI polls this).
    status: str = "ingested"
    drive_folder_id: str | None = None
    drive_index_doc_id: str | None = None
    created_at: str = ""
    updated_at: str = ""
    last_synced_at: str | None = None
    #: Redacted reason of the last failed publish; cleared on success.
    last_publish_error: str | None = None
    #: Zero-pad width used by already-published Doc names (never shrinks).
    pad_width: int = 0
    #: Hash of the index Doc text last written to Drive.
    index_digest: str | None = None


@dataclass
class Video:
    """One video in a playlist and its position in the pipeline."""

    youtube_video_id: str
    title: str = ""
    playlist_id: int | None = None
    id: int | None = None
    position: int = 0
    stage: Stage = Stage.QUEUED
    failure_type: FailureType | None = None
    failure_kind: FailureKind | None = None
    failure_reason: str | None = None
    retry_count: int = 0
    next_retry_at: str | None = None
    drive_doc_id: str | None = None
    removed_upstream: bool = False
    #: Primary key of the first occurrence when this row repeats a video that
    #: appears earlier in the same playlist (PRD section 20); ``None`` otherwise.
    duplicate_of: int | None = None
    #: Bumped when the video is reset/retried so an in-flight task can detect that
    #: its work is stale and must not write.
    run_token: int = 0
    #: Set by "regenerate": the next run regenerates notes even if a summary exists.
    regen_requested: bool = False
    created_at: str = ""
    updated_at: str = ""

    @property
    def auto_retry(self) -> bool:
        """True while a transient failure is waiting for its scheduled automatic retry."""
        return (
            self.stage == Stage.FAILED
            and self.failure_kind == FailureKind.TRANSIENT
            and self.next_retry_at is not None
        )

    @property
    def retries_exhausted(self) -> bool:
        """True when a transient failure used up its automatic retries (manual retry only)."""
        return (
            self.stage == Stage.FAILED
            and self.failure_kind == FailureKind.TRANSIENT
            and self.next_retry_at is None
        )

    @property
    def display_position(self) -> str:
        """Zero-pad the playlist position for table display (design.md 6.2).

        Positions are 1-based everywhere (PRD section 19 numbering starts at 001).
        """
        return f"{self.position:03d}"


@dataclass
class Transcript:
    """Raw transcript text for a video. Soft-deletable for the audit trail."""

    video_id: int
    content: str = ""
    id: int | None = None
    language: str = "en"
    byte_size: int = 0
    content_hash: str | None = None
    fetched_at: str = ""
    deleted_at: str | None = None
    #: Path of the raw subtitle file on disk, removed by a manual cleanup.
    artifact_path: str | None = None

    @property
    def available(self) -> bool:
        return self.deleted_at is None and bool(self.content)


@dataclass
class Summary:
    """Generated Markdown notes for a video, plus its review/publish state."""

    video_id: int
    content: str = ""
    id: int | None = None
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    generated_at: str = ""
    edited: bool = False
    approved: bool = False
    approved_at: str | None = None
    published: bool = False
    published_at: str | None = None
    published_digest: str | None = None
    extra: dict[str, str] = field(default_factory=dict)
