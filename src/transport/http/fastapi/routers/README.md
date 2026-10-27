# routers

## Overview
FastAPI `APIRouter` modules, one per API area. Each exposes a module-level `router` (listed in `__all__`) that the application includes. Handlers are thin: they resolve use cases and adapters from `../dependencies.py`, call them, and map results to `../schemas`.

## Interface
| Module | Prefix | Endpoints |
|---|---|---|
| `drive_auth.py` | `/api/drive` | `GET /status`, `POST /credentials` (upload, size capped by `MAX_CREDENTIALS_BYTES`), `POST /auth` |
| `llm.py` | `/api/llm` | providers and `available-models`; model CRUD, `/models/bulk`, `/models/{pk}/test`; `/fallback` (GET/PUT), `/fallback/rate-limit-usage`, `/fallback/token-usage`; key CRUD, `/keys/check-all`, `/keys/{pk}/reveal`, `/keys/{pk}/cooldowns`, `/keys/{pk}/check` |
| `playlists.py` | `/api/playlists` | ingest (201 new / 200 re-synced), list, get, `/videos`, `/process`, `/reprocess`, delete, `/retry-failed`, `/approve-all`, `/transcripts` (DELETE), `/publish` (202) |
| `settings.py` | `/api/settings` | `GET`, `PUT`, `DELETE` (reset), `GET /notes-prompt` |
| `usage.py` | `/api` | `GET /usage` with provider/model/operation/since/until/cache_status filters |
| `videos.py` | `/api/videos` | `POST /retry-failed`, `GET /{id}`, `GET`/`PUT /{id}/summary`, `POST /{id}/approve`, `/retry`, `/regenerate` (202), `DELETE /{id}/transcript` |

`__init__.py` re-exports only `drive_auth` and `usage`; import the other modules directly. `backend/main.py` includes all six routers directly.

## Invariants
- Long-running actions (process, reprocess, publish, regenerate) return 202 immediately and call `worker.wake()` or `worker.enqueue_publish()`; the work runs in the background worker.
- `POST /api/playlists/{id}/retry-failed` and `POST /api/videos/retry-failed` and `/{id}/retry` wake the worker but return 200, not 202.
- Errors are raised as domain errors or `HTTPError` and rendered by `../errors.py` into the `{code, message, details}` envelope; handlers do not build error bodies themselves.
- Missing playlists/videos yield 404 `NOT_FOUND`.
- `llm.py`: `POST /models/{pk}/test` allows one call per model per `TEST_INTERVAL_SECONDS` (429 with `Retry-After`); model and key updates are `PATCH`; `/fallback/token-usage` has no `response_model` (untyped dict). Handler error codes: `PROVIDER_ERROR` (502), `NOT_FOUND`, `VALIDATION_ERROR` (422).
- `drive_auth.py`: `DRIVE_INVALID_CREDENTIALS_FILE` (400), 413 for oversized uploads. `settings.py`: `SETTINGS_INVALID` / `PROMPT_UNAVAILABLE` (500), 422 `VALIDATION_ERROR` with `details.fields`. `usage.py` falls back to `get_aggregate()` when the ledger lacks `aggregate`.
- `playlists.py` ingest also calls `worker.wake()`.

## Side Effects
- SQLite reads/writes through the repositories; wakes the background worker.
- `drive_auth` and `llm` make outbound network calls (Google OAuth/Drive, LLM provider key and model checks); `drive_auth` writes the uploaded credentials file.
- `llm` mutates the model registry (models, keys, fallback chain, cooldowns).

## Verification
- Run: `pytest` (from repo root)
