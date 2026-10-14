"""LLM infrastructure for the playlist-to-notes pipeline.

Importing this package pulls in **no provider SDK**. ``litellm`` is imported on first use
inside the adapter, so the pipeline, the CLI and the test suite all work with no API key
present. The adapter is reachable through :func:`build_notes_generator`, which builds it from
the ``notes_models`` setting.
"""

from __future__ import annotations

from src.domain.interfaces.notes_generator import GeneratedNotes, NotesGeneratorPort
from src.infrastructure.llm.factory import build_notes_generator

__all__ = [
    "GeneratedNotes",
    "NotesGeneratorPort",
    "build_notes_generator",
]
