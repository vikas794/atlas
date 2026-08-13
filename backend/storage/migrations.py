"""Numbered SQL migrations for the Atlas SQLite store.

Each entry is a ``(version, statements)`` pair applied in a single transaction
and recorded in ``schema_migrations``. Never edit an applied migration — append
a new one.
"""

from __future__ import annotations

MIGRATIONS: list[tuple[str, list[str]]] = [
    (
        "0001_initial",
        [
            """
            CREATE TABLE runs (
                run_id                TEXT PRIMARY KEY,
                cache_key             TEXT,
                search_query          TEXT NOT NULL DEFAULT '',
                normalized_query      TEXT NOT NULL DEFAULT '',
                max_videos            INTEGER NOT NULL DEFAULT 4,
                transcript_language   TEXT NOT NULL DEFAULT 'en',
                status                TEXT NOT NULL DEFAULT 'created',
                created_at            TEXT NOT NULL,
                updated_at            TEXT NOT NULL,
                succeeded_at          TEXT,
                error                 TEXT,
                source_folder         TEXT NOT NULL,
                is_fallback           INTEGER NOT NULL DEFAULT 0,
                transcripts_hash      TEXT,
                summaries_hash        TEXT
            )
            """,
            "CREATE INDEX idx_runs_cache_key ON runs(cache_key)",
            "CREATE INDEX idx_runs_created_at ON runs(created_at)",
            """
            CREATE TABLE videos (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id    TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                video_id  TEXT NOT NULL,
                position  INTEGER NOT NULL,
                data      TEXT NOT NULL,
                UNIQUE (run_id, video_id)
            )
            """,
            "CREATE INDEX idx_videos_run ON videos(run_id)",
            """
            CREATE TABLE transcripts (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id        TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                video_id      TEXT NOT NULL,
                language      TEXT NOT NULL DEFAULT 'en',
                artifact_path TEXT,
                content_hash  TEXT,
                byte_size     INTEGER,
                status        TEXT NOT NULL DEFAULT 'created',
                input_hash    TEXT,
                settings_hash TEXT,
                error         TEXT,
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL,
                UNIQUE (run_id, video_id, language)
            )
            """,
            "CREATE INDEX idx_transcripts_run ON transcripts(run_id)",
            """
            CREATE TABLE summaries (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id        TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                video_id      TEXT NOT NULL,
                artifact_path TEXT,
                content_hash  TEXT,
                byte_size     INTEGER,
                data          TEXT NOT NULL,
                status        TEXT NOT NULL DEFAULT 'created',
                input_hash    TEXT,
                settings_hash TEXT,
                error         TEXT,
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL,
                UNIQUE (run_id, video_id)
            )
            """,
            "CREATE INDEX idx_summaries_run ON summaries(run_id)",
            """
            CREATE TABLE comparisons (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id        TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                data          TEXT,
                content_hash  TEXT,
                status        TEXT NOT NULL DEFAULT 'created',
                input_hash    TEXT,
                settings_hash TEXT,
                error         TEXT,
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL,
                UNIQUE (run_id)
            )
            """,
            """
            CREATE TABLE assignments (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id           TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                video_id         TEXT NOT NULL,
                artifact_path    TEXT,
                content_hash     TEXT,
                byte_size        INTEGER,
                metadata         TEXT,
                display_metadata TEXT,
                status           TEXT NOT NULL DEFAULT 'created',
                input_hash       TEXT,
                settings_hash    TEXT,
                error            TEXT,
                created_at       TEXT NOT NULL,
                updated_at       TEXT NOT NULL,
                UNIQUE (run_id, video_id)
            )
            """,
            "CREATE INDEX idx_assignments_run ON assignments(run_id)",
            """
            CREATE TABLE generation_jobs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id        TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                kind          TEXT NOT NULL,
                status        TEXT NOT NULL,
                input_hash    TEXT,
                settings_hash TEXT,
                error         TEXT,
                created_at    TEXT NOT NULL,
                started_at    TEXT,
                finished_at   TEXT
            )
            """,
            "CREATE INDEX idx_jobs_run ON generation_jobs(run_id)",
            """
            CREATE TABLE cache_entries (
                cache_key        TEXT PRIMARY KEY,
                kind             TEXT NOT NULL,
                run_id           TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                normalized_query TEXT NOT NULL DEFAULT '',
                settings_hash    TEXT,
                status           TEXT NOT NULL,
                created_at       TEXT NOT NULL,
                expires_at       TEXT NOT NULL,
                hit_count        INTEGER NOT NULL DEFAULT 0,
                last_hit_at      TEXT
            )
            """,
            "CREATE INDEX idx_cache_expiry ON cache_entries(expires_at)",
        ],
    ),
    (
        "0002_usage_ledger_and_extended_cache",
        [
            """
            CREATE TABLE IF NOT EXISTS usage_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                run_id TEXT,
                timestamp TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                operation TEXT NOT NULL,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                estimated_cost_usd REAL NOT NULL DEFAULT 0.0,
                currency TEXT NOT NULL DEFAULT 'USD',
                latency_ms INTEGER NOT NULL DEFAULT 0,
                success INTEGER NOT NULL DEFAULT 1,
                error_category TEXT,
                cache_hit INTEGER NOT NULL DEFAULT 0,
                cache_key TEXT,
                cache_namespace TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                metadata TEXT,
                created_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_usage_timestamp ON usage_ledger(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_usage_provider ON usage_ledger(provider)",
            "CREATE INDEX IF NOT EXISTS idx_usage_model ON usage_ledger(model)",
            "CREATE INDEX IF NOT EXISTS idx_usage_operation ON usage_ledger(operation)",
            "CREATE INDEX IF NOT EXISTS idx_usage_run_id ON usage_ledger(run_id)",
            "CREATE INDEX IF NOT EXISTS idx_usage_request_id ON usage_ledger(request_id)",
            "ALTER TABLE cache_entries ADD COLUMN namespace TEXT NOT NULL DEFAULT 'legacy'",
            "ALTER TABLE cache_entries ADD COLUMN version TEXT NOT NULL DEFAULT 'v1'",
            "ALTER TABLE cache_entries ADD COLUMN content_hash TEXT",
            "ALTER TABLE cache_entries ADD COLUMN params_hash TEXT",
            "ALTER TABLE cache_entries ADD COLUMN value BLOB",
        ],
    ),
]
