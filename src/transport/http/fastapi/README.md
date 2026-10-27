# fastapi

## Overview
FastAPI adapter of the HTTP transport: dependency wiring (composition root), error mapping, request middleware, routers and schemas. It is the only layer that knows both the application ports and the infrastructure implementing them.

## Interface
- `dependencies.py`: cached factories (`get_settings_loader`, `get_playlist_repo`, `get_transcript_provider`, `get_model_registry`, `get_notes_generator`, `get_drive_exporter`, `get_drive_publisher`, `get_settings_repo`); uncached `get_usage_ledger`; module-level singletons `get_youtube_provider` and `get_worker`; use-case factories (`get_ingest_playlist_use_case`, `get_process_video_use_case`, `get_publish_playlist_use_case`, `get_approve_*`, `get_retry_*`, `get_regenerate_video_use_case`, `get_reprocess_playlist_use_case`, `get_delete_playlist_use_case`, `get_cleanup_transcripts_use_case`, `get_save_summary_edit_use_case`); `get_settings_loader_dep`, `get_storage_settings`; private `_LazyYouTubeProvider` (resolves the real provider on first use, so a missing key fails only requests that reach YouTube); settings lifecycle (`remember_boot_settings`, `get_boot_settings`, `apply_settings`); `reset_dependency_caches`.
- `errors.py`: `HTTPError`, `error_envelope`, `domain_error_to_http`, handlers (`domain_error_handler`, `http_error_handler`, `validation_error_handler`, `starlette_http_error_handler`, `pipeline_error_handler`, `generic_error_handler`) and `register_error_handlers(app)`.
- `middleware.py`: `RequestLoggingMiddleware`.
- Subpackages: [routers](./routers/README.md), [schemas](./schemas/README.md).

## Invariants
- Adapters are built lazily; importing the app needs no API key. A missing key raises `ConfigurationError` (503 `CONFIG_MISSING`) at use time and the failure is never cached (YouTube provider).
- Every error response is `{code, message, details}`; message and details pass through `redact_text`.
- Status mapping: `DriveAuthorizationRequired` 409 `DRIVE_AUTH_REQUIRED`, `ConfigurationError` 503, `NotFoundError` 404, `InvalidTransitionError` 409 `INVALID_STATE`, `ConflictError` 409, `ProviderError` 502 `PROVIDER_ERROR_<PROVIDER>`, `CacheError` 503, `StorageError` 500, validation 422. Domain and HTTP errors log 5xx at WARNING and 4xx at INFO; unhandled exceptions log at ERROR with a traceback.
- `pipeline_error_handler`: `INVALID_URL` 400, `PLAYLIST_UNAVAILABLE` 404, other transient 503, other permanent 400, else 500; code is `failure_type.value.upper()`. `DomainError` fallback is 500 `DOMAIN_ERROR`; unhandled exceptions are 500 `INTERNAL_ERROR`; `starlette_http_error_handler` emits `HTTP_<status>` (and does not log when `detail` is a dict).
- A client `X-Correlation-ID` is trusted only if it matches `[A-Za-z0-9._-]{1,64}`; otherwise a UUID is generated.
- Any new `lru_cache` factory must be added to `_CACHED_FACTORIES` so `reset_dependency_caches` clears it.
- `apply_settings` also calls `cache_clear()` on `get_settings_loader` and `get_notes_generator`, and mutates `transcript_provider._min_interval` / `_settings` and the publisher's `max_retries` / `backoff_*` in place; running jobs keep the values they started with. Restart-only settings are untouched.

## Side Effects
- `get_model_registry` seeds the registry and sweeps expired cooldowns and old usage rows once per process.
- Repository construction runs DB migrations on first use.
- Module-level singletons (`_worker`, `_youtube_provider`, `_boot_settings`).
- Middleware logs each request (successful GET/HEAD at DEBUG, everything else at INFO) and sets the `X-Correlation-ID` response header.

## Verification
- Run: `pytest` (from repo root)
