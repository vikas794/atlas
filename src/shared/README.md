# src/shared

## Overview
Small helpers used by the outer layers (`configure_logging` lazily imports `src.config.settings.repo_root` for file logging): API key rotation, secret redaction, logging setup, atomic file and text utilities, and OAuth client file validation.

## Interface
- `api_keys.read_api_keys(env_name)`: all keys from `NAME`, comma/semicolon/whitespace lists and `NAME_2`, `NAME_3`, ...
- `api_keys.KeyPool`: round-robin pool; `acquire(exclude=())` returns the next usable key or `None`; `cool_down`, `disable`; `status()` (counts: total/available/cooling/disabled); `keys`, `len()`; `pool_for(env_name)` returns the shared pool; `reset_pools()`; `DEFAULT_COOLDOWN_SECONDS`, `LONG_COOLDOWN_SECONDS`.
- `redaction.redact_text(text, *secrets)`: blanks known credential shapes, `Authorization: Bearer ...` values and `key=value` assignments; explicit `*secrets` are replaced verbatim only when at least 8 characters; `REDACTED`.
- `logging_setup.configure_logging(settings=None, *, stream=None)`: installs console, optional rotating file and recent-errors handlers; returns the console handler. Also `AtlasFormatter`, `RecentErrorsHandler`, `RECENT_ERRORS`, `recent_errors()`. `RecentErrorsHandler` keeps the last 50 WARNING+ records; `snapshot()` is newest first; `clear()` empties it (used by `DELETE /api/diagnostics/recent-errors`).
- `files.delete_files(paths, *, root=None)`: deletes files, optionally confined to `root`; returns the count removed.
- `oauth_client.client_secrets_problem(raw)`: reason a Google OAuth client file is unusable, or `None`.
- `text`: `sha256_text(content)`, `atomic_write(path, content, encoding="utf-8")`, `ensure_output_folder(folder_path)` (creates it, returns the absolute path), `load_json_with_recovery(raw)` (repairs raw newlines, carriage returns and tabs inside JSON strings), `clean_srt_content(raw_srt)` (strips cues and timestamps, groups four sentences per paragraph).

## Invariants
- Key values are never logged by this package; every formatted log line passes through `redact_text`.
- `KeyPool` is thread-safe. A pool is rebuilt when the configured keys change; cooldowns persist otherwise.
- `delete_files` with a `root` refuses paths outside it and never raises on a locked or missing file.
- `atomic_write` replaces the target in one step (temp file, fsync, `os.replace`).
- `configure_logging` is idempotent: it swaps its own handlers instead of stacking them.
- `client_secrets_problem` messages never contain values from the file.

## Side Effects
- `configure_logging` mutates the root logger and levels of noisy third-party loggers; may create `logs/atlas.log`.
- `atomic_write`, `ensure_output_folder` and `delete_files` touch the file system.
- `api_keys` reads environment variables; pools are module-level state.

## Verification
- Run: `uv run pytest -q tests/test_api_key_rotation.py tests/test_redaction.py tests/test_logging_setup.py tests/test_recent_errors.py`
