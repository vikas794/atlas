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
