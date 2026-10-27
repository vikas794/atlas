# schemas

## Overview
Pydantic request/response models for the Atlas HTTP API. Data contracts; no I/O except `playlists.py` `_max_auto_retries()`, which calls `load_settings()` on first use.
Modules are imported directly by routers (`from ...schemas.llm import ...`).

## Interface
- `diagnostics.py`: `DiagnosticCheck`, `RecentError`, `DiagnosticsResponse`.
- `drive_auth.py`: `DriveStatusResponse`, `DriveActionResponse`.
- `llm.py`: `_Strict` (base that rejects unknown fields), provider/model/key/fallback models (`ProviderOut`, `ProvidersResponse`, `ModelOut`, `ModelsResponse`, `ModelCreate`, `ModelUpdate`, `ModelBulkCreate`, `ModelBulkResponse`, `ModelTestResponse`, `FallbackEntryOut`, `FallbackResponse`, `ChainEntryIn`, `ChainUpdate`, `KeyOut`, `KeysResponse`, `KeyCreate`, `KeyUpdate`, `KeyReveal`, `CooldownOut`, `ClearedResponse`, `AvailableModelsResponse`, `RateLimitUsageResponse`).
- `playlists.py`: `IngestPlaylistRequest`, `PlaylistResponse`, `PlaylistListResponse`, `VideoResponse` (with `from_domain`), `VideoListResponse`, `SummaryResponse` (also with `from_domain`), `SaveSummaryRequest`, `RegenerateRequest`, `NotesPromptResponse`, `ActionResponse`, `PublishResponse`.
- `settings.py`: `SettingGroup`, `SettingField`, `SettingsResponse`, `SettingsUpdateRequest`.
- `usage.py`: `UsageProviderAggregate`, `UsageOperationAggregate`, `UsageCacheAggregate`, `UsageTimeRange`, `UsageAggregateResponse`.
- `__init__.py` re-exports only the `drive_auth` and `usage` submodules.

## Invariants
- Input models derived from `_Strict` (in `llm.py`) reject unknown fields.
- `playlists.py` caches `_max_auto_retries`; `dependencies.apply_settings` calls its `cache_clear()`, so keep that name.
- API keys are exposed only through `KeyReveal`; inbound `KeyCreate` / `KeyUpdate` carry a raw `key`, while `KeyOut` exposes `masked_key`, `auto_disabled`, `cooldowns: list[CooldownOut]` and the monthly cap fields.
- No schema exists for `GET /fallback/token-usage` (untyped dict).

## Side Effects
- None at import beyond the `_max_auto_retries` cache in `playlists.py`.

## Verification
- Run: `pytest` (from repo root)
