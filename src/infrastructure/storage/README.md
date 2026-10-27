# storage

## Overview
SQLite persistence layer: connection setup, ordered migrations, the playlist/video/transcript/summary repository, settings storage and the usage ledger.

## Interface
- `db.py`: `connect(db_path)`, `initialize(db_path)`, `transaction(conn)`, `now_iso()`, `now_epoch()`, `epoch_of(iso)`.
- `migrations.py`: `MIGRATIONS` (ordered `(version, statements)`; latest are `0005_app_settings`, `0006_regen_prompt`, `0007_model_registry`), `FOREIGN_KEYS_OFF`.
- `playlist_repo.py`: `PlaylistRepository` (async) covering playlists (create/get/list/update/delete, publish state, Drive ids), videos (insert, list, stage updates, retries, upstream-removal flags, queue and recovery queries), transcripts (`soft_delete_transcript`, `get_transcript_artifact_paths`), summaries (upsert, copy, edit marking), approval (`approve_all_pending`), publish marking (`begin_publish`/`finish_publish`) and regenerate. Also `set_pad_width`, `set_index_digest`, `touch_synced`, `count_playlists`/`count_videos`/`count_by_stage`/`count_published`, `max_position`, `reset_playlist`, `requeue_all_failed`.
- `settings_repo.py`: `SqlSettingsRepository` with `load_all()`, `replace(values)`, `clear()`.
- `sql.py`: `SqlUsageLedger` (`UsageLedgerPort`) with `record`, `aggregate`, `recent`, `record_usage`, `get_aggregate`, `get_by_provider`, `get_by_operation`, `get_cache_stats`.

## Invariants
- Connections use WAL, `foreign_keys=ON`, a 30s busy timeout and autocommit; write boundaries use `transaction`.
- Migrations apply in order and applied versions are never rewritten; `0004_duplicates_publish_state` runs with foreign keys off.
- A populated database is copied to `<db>.pre-<version>.bak` (via `sqlite3.backup`, not a file copy) before its first pending migration (best effort; skipped if the target already exists).
- `SqlUsageLedger` creates `usage_ledger` itself (`CREATE TABLE IF NOT EXISTS` in `_ensure_table`), independent of migration `0002`.

## Side Effects
- Creates the database directory and file; writes `.bak` copies.
- All repository calls read/write SQLite.

## Verification
- Run: `pytest` (from repo root)
