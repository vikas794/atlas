"""Port for generating per-video Markdown notes from a transcript.

PRD section 18: notes come from a versioned prompt file, each generation records
the prompt version and the model that produced it, and malformed output is a
transient failure the pipeline retries. This module owns only the *contract* --
the result shape -- so the pipeline never imports a provider SDK. Choosing and
building an adapter is infrastructure: see ``src.infrastructure.llm.factory``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class GeneratedNotes:
    """Provider-agnostic result of one notes generation.

    ``prompt_version`` is what ends up on ``Summary.prompt_version`` so a run can
    be traced back to the exact prompt text that produced the notes (PRD 18).
    """

    content: str
    provider: str
    model: str
    prompt_version: str


class NotesGeneratorPort(Protocol):
    """Generates Markdown notes for one video."""

    async def generate_notes(self, *, title: str, transcript: str) -> GeneratedNotes:
        """Generate notes for ``transcript``.

        Args:
            title: The video title, substituted into the prompt template.
            transcript: The raw transcript text. Never logged, never persisted
                by the adapter itself.

        Returns:
            Validated notes content plus the provider/model/prompt that made it.

        Raises:
            NotesGenerationError: On a classified pipeline failure. Transient
                kinds are retried by ``ProcessVideoUseCase`` (the single owner of
                retries), not by the adapter.
            NotesGeneratorConfigError: When Atlas itself is misconfigured (missing
                API key, missing prompt file). Never retried -- no number of
                attempts fixes a missing key.
        """
        ...


__all__ = [
    "GeneratedNotes",
    "NotesGeneratorPort",
]
