# backend/services

## Overview
Backend-only services that sit beside the FastAPI app and are not part of the pipeline domain. Today this is the first-run diagnostics report behind `GET /api/diagnostics`.

## Interface
- `diagnostics.Check`: frozen dataclass (`id`, `label`, `status`, `message`); `status` is `ok`, `warning` or `error`. `label` comes from `CHECK_LABELS` (exported).
- `diagnostics.run_checks(dist_dir)`: runs every check and returns `list[Check]`.
- `diagnostics.diagnostics_report(dist_dir)`: dict for the `DiagnosticsResponse` schema (checks plus the redacted recent log errors).
- Individual checks: `check_config()` (no argument; returns `(Check, AtlasSettings | None)`); `check_database`, `check_youtube_key`, `check_llm_key`, `check_drive_credentials`, `check_drive_token` take `settings`; `check_frontend_build(dist_dir)`.

## Invariants
- A check never returns a secret value. Keys are reported as set or not set, files by path.
- `error` means a core feature cannot work (for example no YouTube key); `warning` means Atlas runs but one feature is unavailable (for example Drive before consent).
- A Drive token that exists on disk is not reported `ok` unless it is valid or refreshable.
- Invalid config produces a `Check`, not an exception; `run_checks` then reports the database, youtube_key, llm_key, drive_credentials and drive_token checks as `warning` ("Not checked: fix the configuration first").
- `check_drive_token` reports a network failure during refresh as `warning`, never raises.

## Side Effects
- Reads the config files, the SQLite database (read-only queries on the model-registry tables `llm_models`, `llm_fallback`, `llm_api_keys`), API key environment variables (via `read_api_keys`) and the Drive credential and token files.
- Makes one network call: a Google token refresh when the saved Drive token has expired (`check_drive_token` builds a `GoogleDriveExporter` and calls `ensure_authorized()`).

## Verification
- Run: `uv run pytest -q tests/test_diagnostics.py`
