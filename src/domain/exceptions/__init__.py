from __future__ import annotations


class DomainError(Exception):
    """Base class for all domain layer errors."""


class ProviderError(DomainError):
    """Raised when an external provider (LLM, transcript API, etc.) fails."""

    def __init__(self, message: str, provider: str | None = None) -> None:
        super().__init__(message)
        self.provider = provider


class CacheError(DomainError):
    """Raised when cache operations fail."""


class StorageError(DomainError):
    """Raised when artifact or run storage operations fail."""


class ConfigurationError(DomainError):
    """Raised when Atlas itself is misconfigured (e.g. a required API key is unset).

    No number of retries fixes this, so it is never classified as transient. The
    HTTP layer maps it to ``503 CONFIG_MISSING`` with a message naming the
    environment variable that must be set.
    """


class NotFoundError(DomainError):
    """Raised when a referenced playlist, video or summary does not exist (HTTP 404)."""


class ConflictError(DomainError):
    """Raised when the requested action is invalid for the current state (HTTP 409)."""


class StaleRunError(DomainError):
    """Raised when an in-flight pipeline task discovers its video was reset or deleted.

    The task must stop without writing anything: a reprocess, retry or delete
    happened while it was running, so its results are stale (PRD section 15).
    """


class NotesGeneratorConfigError(ConfigurationError, ValueError):
    """Atlas itself is misconfigured for notes generation: no prompt file, no API key,
    bad template.

    This is deliberately *not* a ``PipelineError``. The failure taxonomy in PRD
    section 8 has no "your machine is not set up" member, and borrowing a transient
    one would make the worker burn its whole retry budget waiting for a missing key
    that will still be missing on the last attempt. The pipeline records it as a
    permanent ``config_error`` with a clear reason.
    """
