# application

## Overview
Application layer: use cases and the background worker that drives the pipeline over domain ports.

## Interface
- `worker.py`: `PlaylistWorker(repo, process_video, publish=None, max_concurrent_videos=2, poll_interval_seconds=2.0, shutdown_timeout_seconds=5.0)` with `start`, `stop`, `wake`, `enqueue_publish(playlist_pk)`. `wake` and `enqueue_publish` are non-blocking triggers.
- Worker loop: a notes pool (`phase="notes"`, `max_concurrent_videos` slots) for `TRANSCRIPT_READY` videos; a fetch pool (`phase="fetch"`, 1 slot) for `QUEUED` videos and `list_transient_due` retries; and a publish queue deduplicated via `_publish_queue` and `_publishing_ids`.
- `start` is async and does crash recovery: `repo.recover_in_flight_videos()`, and re-enqueues `list_publishing_playlists()` when `publish` is set. `stop` is async, waits up to `shutdown_timeout_seconds` for active jobs, then cancels the rest.
- [./use_cases/README.md](./use_cases/README.md) - use case package; see [playlist](./use_cases/playlist/README.md).

## Invariants
- `worker.py` depends only on `domain` and `application.use_cases`; the use cases also import `src.shared` helpers (see use_cases). Never imports infrastructure or transport.
- `src/application` has no `__init__.py` (namespace package).

## Side Effects
- Spawns and cancels asyncio tasks, reads and updates repository state, triggers network calls through use cases.

## Verification
- Run: `pytest`
