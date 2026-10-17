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

#: ``Summary.prompt_version`` of notes made with a one-off prompt (temporary regenerate).
CUSTOM_PROMPT_VERSION = "custom"

#: The placeholders every prompt template must contain.
PROMPT_PLACEHOLDERS = ("{{Title}}", "{{Transcript}}")

#: Longest one-off prompt accepted from a regenerate request.
MAX_CUSTOM_PROMPT_CHARS = 20_000


def missing_placeholders(template: str) -> list[str]:
    """Placeholders absent from ``template`` (empty when it is usable)."""
    return [p for p in PROMPT_PLACEHOLDERS if p not in template]


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

    async def generate_notes(
        self, *, title: str, transcript: str, prompt_template: str | None = None
    ) -> GeneratedNotes:
        """Generate notes for ``transcript``.

        Args:
            title: The video title, substituted into the prompt template.
            transcript: The raw transcript text. Never logged, never persisted
                by the adapter itself.
            prompt_template: A one-off prompt (with ``{{Title}}`` and
                ``{{Transcript}}``) used instead of the prompt file; the result is
                then recorded with ``prompt_version="custom"``. ``None`` uses the file.

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
    "CUSTOM_PROMPT_VERSION",
    "MAX_CUSTOM_PROMPT_CHARS",
    "PROMPT_PLACEHOLDERS",
    "GeneratedNotes",
    "missing_placeholders",
    "NotesGeneratorPort",
]
