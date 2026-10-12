"""Shared types for the LLM adapters.

Only the settings accessor protocol lives here. Retries are deliberately *not* part of the
LLM layer: ``ProcessVideoUseCase`` is the single owner of retry and backoff, so an adapter
makes exactly one provider call per attempt.
"""

from __future__ import annotations

from typing import Protocol


class SettingsLoader(Protocol):
    """Minimal surface area that providers need from the application config.

    The default implementation is :meth:`src.config.settings.AtlasSettings.to_loader`,
    but adapters accept any object that satisfies this protocol so they
    remain trivially testable.
    """

    def __call__(self, key_path: str, default: object = None) -> object:
        ...


__all__ = ["SettingsLoader"]
