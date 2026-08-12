"""Versioned SQLite pipeline-storage layer.

Owns runs, video metadata, structured summaries, comparisons, assignments,
generation jobs, and cache state. Transcript SRT and assignment Markdown stay
on disk under the managed artifact root; structured JSON lives in SQLite JSON
text columns.
"""

from backend.storage.repository import (
    STATUS_CREATED,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_STALE,
    STATUS_SUCCEEDED,
    RunRepository,
    get_repository,
)

__all__ = [
    "RunRepository",
    "STATUS_CREATED",
    "STATUS_RUNNING",
    "STATUS_SUCCEEDED",
    "STATUS_FAILED",
    "STATUS_STALE",
    "get_repository",
]
