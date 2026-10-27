# application/use_cases/playlist

## Overview
Use cases orchestrating the playlist pipeline (ingest, process, review actions, publish) over domain ports. Each use case receives its ports via constructor injection.

## Interface
- `ingest.py`: `IngestPlaylistUseCase` (`execute`, `ingest`), `IngestResult`; provider calls run via `asyncio.to_thread` and a `playlist_fetch` record is written to `UsageLedgerPort`.
- `process_video.py`: `ProcessVideoUseCase(..., max_auto_retries=3, backoff_base_seconds=5.0, backoff_cap_seconds=60.0, rate_limit_max_auto_retries/_backoff_base_seconds/_backoff_cap_seconds=None)` (rate-limit values default to the general ones); `execute(video_pk, phase="all"|"fetch"|"notes") -> Video | None`; `INTERNAL_ERROR_REASON`.
- `publish.py`: `PublishPlaylistUseCase` (`request_publish`, `run`, `execute`); `request_publish` raises `ConflictError` when `begin_publish` returns False; `run` calls `finish_publish` with a redacted reason or `None` and never raises; `execute` handles `DriveFolderMissing` by forgetting Drive ids and republishing; internal `_PersistingProgress` persists Drive progress.
- `actions.py`: `ApproveVideoUseCase`, `ApproveAllUseCase`, `SaveSummaryEditUseCase`, `RetryVideoUseCase`, `RetryAllFailedUseCase`, `RegenerateVideoUseCase`, `ReprocessPlaylistUseCase`, `DeletePlaylistUseCase`, `CleanupTranscriptsUseCase` (`execute_video`, `execute_playlist`), `EDITABLE_STAGES`. `RegenerateVideoUseCase.execute(video_pk, prompt=None)`, `RetryAllFailedUseCase.execute(playlist_pk=None)`; `ReprocessPlaylistUseCase` and `DeletePlaylistUseCase` return `bool`.
- `__init__.py` re-exports 11 use cases but not `PublishPlaylistUseCase` (import it from `publish.py`).

## Invariants
- Depend on `domain` ports, models and services, plus `src.shared.redaction`/`text`/`files` and (in `ingest.py`) `sqlite3`; never on concrete adapters.
- Stage transitions are validated by `domain.services.stage_machine`.
- Removed-upstream videos refuse `RetryVideoUseCase` and `RegenerateVideoUseCase` (`_refuse_if_removed_upstream`); `SaveSummaryEditUseCase` does not check it, only that a summary exists; summary edits are allowed only in `EDITABLE_STAGES`; custom prompts are validated.
- `ProcessVideoUseCase` alone records unexpected errors with `INTERNAL_ERROR_REASON` rather than raw details, and maps `ConfigurationError` to `CONFIG_ERROR`; duplicate rows reuse the first occurrence's summary (`_reuse_first_occurrence`).
- Publish is guarded by `begin_publish`/`finish_publish` so one publish runs per playlist.

## Side Effects
- Persist state through `PlaylistRepositoryPort`; call transcript, notes, playlist-provider and Drive ports (network); delete transcript artifacts; emit logs.

## Verification
- Run: `pytest`
