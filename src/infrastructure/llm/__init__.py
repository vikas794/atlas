"""LLM infrastructure for the playlist-to-notes pipeline.

Importing this package pulls in **no provider SDK**. ``google.genai`` and
``openai`` are imported inside the adapters' client factories, so the pipeline,
the CLI and the test suite all work with no API key present. The concrete
adapters are reachable through :func:`build_notes_generator`, which imports them
on demand based on ``notes_provider``.
"""

from __future__ import annotations

from src.domain.interfaces.notes_generator import GeneratedNotes, NotesGeneratorPort
from src.infrastructure.llm.factory import build_notes_generator

__all__ = [
    "GeneratedNotes",
    "NotesGeneratorPort",
    "build_notes_generator",
]
