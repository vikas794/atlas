# domain/models

## Overview
Core entities and pipeline vocabulary for playlists and their videos.

## Interface
- `playlist.py`: `Stage` (pipeline stages, `is_terminal`), `FailureKind`, `FailureType`, `FAILURE_KINDS`, `kind_of`, `PipelineError`, `TranscriptError`, `NotesGenerationError`, dataclasses `Playlist` (`status`, `pad_width`, `index_digest`), `Video` (`auto_retry`, `retries_exhausted`, `display_position`; `run_token`, `regen_requested`, `regen_prompt`, `duplicate_of`, `removed_upstream`), `Transcript` (`available`), `Summary`.

## Invariants
- `Stage.PUBLISHED` is the only terminal stage.
- Every `FailureType` maps to a `FailureKind` in `FAILURE_KINDS`; add both together.
- `PipelineError(failure_type, reason, *, diagnostic=None)` exposes `failure_type`, `failure_kind` (derived via `kind_of`), `reason` and a log-safe `diagnostic`; `kind_of` defaults unmapped types to `PERMANENT`.
- No infrastructure imports.

## Side Effects
- None.

## Verification
- Run: `pytest`
