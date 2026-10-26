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
    (
        "0003_playlist_pipeline",
        [
            # --- Retire the legacy search-run schema -------------------------------
            # Dropped child-first: every table below references runs(run_id) and
            # foreign_keys=ON, so a parent cannot be dropped before its children.
            # usage_ledger intentionally survives (its run_id is plain TEXT, not an
            # FK) so the usage dashboard keeps its history.
            "DROP TABLE IF EXISTS cache_entries",
            "DROP TABLE IF EXISTS generation_jobs",
            "DROP TABLE IF EXISTS assignments",
            "DROP TABLE IF EXISTS comparisons",
            "DROP TABLE IF EXISTS summaries",
            "DROP TABLE IF EXISTS transcripts",
            "DROP TABLE IF EXISTS videos",
            "DROP TABLE IF EXISTS runs",
            # --- Playlist ingestion -------------------------------------------------
            """
            CREATE TABLE playlists (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_id        TEXT NOT NULL UNIQUE,
                source_url         TEXT NOT NULL,
                title              TEXT NOT NULL DEFAULT '',
                video_count        INTEGER NOT NULL DEFAULT 0,
                status             TEXT NOT NULL DEFAULT 'ingested',
                drive_folder_id    TEXT,
                drive_index_doc_id TEXT,
                created_at         TEXT NOT NULL,
                updated_at         TEXT NOT NULL,
                last_synced_at     TEXT
            )
            """,
            "CREATE INDEX idx_playlists_created ON playlists(created_at)",
            # --- Videos ---------------------------------------------------------------
            # UNIQUE (playlist_id, youtube_video_id): a video appearing twice in one
            # playlist is stored and processed once. Its first YouTube position is
            # kept verbatim, so published Doc numbering can contain gaps.
            """
            CREATE TABLE videos (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_id       INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
                youtube_video_id  TEXT NOT NULL,
                title             TEXT NOT NULL DEFAULT '',
                position          INTEGER NOT NULL DEFAULT 0,
                stage             TEXT NOT NULL DEFAULT 'queued',
                failure_type      TEXT,
                failure_kind      TEXT,
                failure_reason    TEXT,
                retry_count       INTEGER NOT NULL DEFAULT 0,
                next_retry_at     TEXT,
                drive_doc_id      TEXT,
                removed_upstream  INTEGER NOT NULL DEFAULT 0,
                created_at        TEXT NOT NULL,
                updated_at        TEXT NOT NULL,
                UNIQUE (playlist_id, youtube_video_id)
            )
            """,
            "CREATE INDEX idx_videos_playlist_stage ON videos(playlist_id, stage)",
            "CREATE INDEX idx_videos_playlist_position ON videos(playlist_id, position)",
            "CREATE INDEX idx_videos_youtube ON videos(youtube_video_id)",
            # --- Transcripts ---------------------------------------------------------
            # Soft delete (deleted_at) keeps the audit row per PRD section 7.
            """
            CREATE TABLE transcripts (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id     INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                content      TEXT NOT NULL,
                language     TEXT NOT NULL DEFAULT 'en',
                byte_size    INTEGER NOT NULL DEFAULT 0,
                content_hash TEXT,
                fetched_at   TEXT NOT NULL,
                deleted_at   TEXT,
                UNIQUE (video_id)
            )
            """,
            "CREATE INDEX idx_transcripts_video ON transcripts(video_id)",
            # --- Summaries -----------------------------------------------------------
            # published_digest lets the UI show "Edited since publishing" by
            # comparing the live content hash against the hash at publish time.
            """
            CREATE TABLE summaries (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id         INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                content          TEXT NOT NULL,
                provider         TEXT,
                model            TEXT,
                prompt_version   TEXT,
                generated_at     TEXT NOT NULL,
                edited           INTEGER NOT NULL DEFAULT 0,
                approved         INTEGER NOT NULL DEFAULT 0,
                approved_at      TEXT,
                published        INTEGER NOT NULL DEFAULT 0,
                published_at     TEXT,
                published_digest TEXT,
                UNIQUE (video_id)
            )
            """,
            "CREATE INDEX idx_summaries_video ON summaries(video_id)",
        ],
    ),
    (
        "0004_duplicates_publish_state",
        [
            # --- Publish state on the playlist (PRD section 16: publish runs in the
            # background worker and the UI polls these). pad_width remembers the
            # zero-pad width of already-published Doc names; index_digest the hash
            # of the index Doc text last written to Drive.
            "ALTER TABLE playlists ADD COLUMN last_publish_error TEXT",
            "ALTER TABLE playlists ADD COLUMN pad_width INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE playlists ADD COLUMN index_digest TEXT",
            # --- Raw subtitle artifact of a transcript, so a manual cleanup can delete
            # the file as well as the DB text (PRD FR11).
            "ALTER TABLE transcripts ADD COLUMN artifact_path TEXT",
            # --- videos: one row per playlist POSITION (PRD section 20). The old
            # UNIQUE (playlist_id, youtube_video_id) silently dropped repeated videos
            # and made numbering disagree with YouTube. The new table keeps ids (so
            # transcripts/summaries keep pointing at the same rows), replaces that
            # constraint with UNIQUE (playlist_id, position), and adds:
            #   duplicate_of_video_id - first occurrence of a repeated video
            #   run_token             - bumped on reset/retry; stale tasks must not write
            #   regen_requested       - "regenerate notes" was requested (worker job)
            # Positions become 1-based: a playlist stored 0-based (it has a row at
            # position 0) is shifted by one so numbering starts at 001. This runs with
            # foreign keys OFF (see FOREIGN_KEYS_OFF) because dropping the old table
            # would otherwise cascade-delete every transcript and summary.
            """
            CREATE TABLE videos_new (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_id           INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
                youtube_video_id      TEXT NOT NULL,
                title                 TEXT NOT NULL DEFAULT '',
                position              INTEGER NOT NULL DEFAULT 1,
                stage                 TEXT NOT NULL DEFAULT 'queued',
                failure_type          TEXT,
                failure_kind          TEXT,
                failure_reason        TEXT,
                retry_count           INTEGER NOT NULL DEFAULT 0,
                next_retry_at         TEXT,
                drive_doc_id          TEXT,
                removed_upstream      INTEGER NOT NULL DEFAULT 0,
                duplicate_of_video_id INTEGER REFERENCES videos(id) ON DELETE SET NULL,
                run_token             INTEGER NOT NULL DEFAULT 0,
                regen_requested       INTEGER NOT NULL DEFAULT 0,
                created_at            TEXT NOT NULL,
                updated_at            TEXT NOT NULL,
                UNIQUE (playlist_id, position)
            )
            """,
            """
            INSERT INTO videos_new (
                id, playlist_id, youtube_video_id, title, position, stage, failure_type,
                failure_kind, failure_reason, retry_count, next_retry_at, drive_doc_id,
                removed_upstream, created_at, updated_at
            )
            SELECT
                v.id, v.playlist_id, v.youtube_video_id, v.title,
                v.position + CASE WHEN EXISTS (
                    SELECT 1 FROM videos z WHERE z.playlist_id = v.playlist_id AND z.position = 0
                ) THEN 1 ELSE 0 END,
                v.stage, v.failure_type, v.failure_kind, v.failure_reason, v.retry_count,
                v.next_retry_at, v.drive_doc_id, v.removed_upstream, v.created_at, v.updated_at
            FROM videos v
            """,
            "DROP TABLE videos",
            "ALTER TABLE videos_new RENAME TO videos",
            "CREATE INDEX idx_videos_playlist_stage ON videos(playlist_id, stage)",
            "CREATE INDEX idx_videos_youtube ON videos(playlist_id, youtube_video_id)",
            "CREATE INDEX idx_videos_duplicate_of ON videos(duplicate_of_video_id)",
        ],
    ),
    (
        "0005_app_settings",
        [
            # UI-edited setting overrides (one row per changed setting). No row means the
            # value comes from config.yaml / the default; env vars still win over a row.
            """
            CREATE TABLE app_settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
        ],
    ),
    (
        "0006_regen_prompt",
        [
            # One-off prompt template for a pending "regenerate notes" run (set by the
            # regenerate request, cleared when the new notes are stored).
            "ALTER TABLE videos ADD COLUMN regen_prompt TEXT",
        ],
    ),
    (
        "0007_model_registry",
        [
            # LLM model registry (modelled on FreeLLMAPI): models, keys, one ordered fallback
            # chain, cooldowns and the rate-limit / quota ledgers. Keys are stored in plain text.
            """
            CREATE TABLE llm_models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                model_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                litellm_model TEXT NOT NULL,
                api_base TEXT,
                input_token_limit INTEGER NOT NULL DEFAULT 128000,
                request_timeout_seconds REAL,
                rpm_limit INTEGER,
                rpd_limit INTEGER,
                tpm_limit INTEGER,
                tpd_limit INTEGER,
                enabled INTEGER NOT NULL DEFAULT 1,
                source TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL,
                UNIQUE (provider, model_id)
            )
            """,
            """
            CREATE TABLE llm_api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                label TEXT NOT NULL,
                key_value TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'unknown',
                enabled INTEGER NOT NULL DEFAULT 1,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                last_checked_at TEXT,
                last_healthy_at TEXT,
                last_health_error TEXT,
                monthly_request_cap INTEGER,
                monthly_token_cap INTEGER,
                created_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX idx_llm_keys_provider ON llm_api_keys(provider)",
            """
            CREATE TABLE llm_fallback (
                model_id INTEGER PRIMARY KEY REFERENCES llm_models(id) ON DELETE CASCADE,
                priority INTEGER NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1
            )
            """,
            # model_id = '*' cools the whole key down (rejected key, no credit).
            """
            CREATE TABLE llm_cooldowns (
                key_id INTEGER NOT NULL REFERENCES llm_api_keys(id) ON DELETE CASCADE,
                model_id TEXT NOT NULL,
                expires_at REAL NOT NULL,
                source TEXT NOT NULL DEFAULT 'heuristic',
                set_at REAL NOT NULL,
                PRIMARY KEY (key_id, model_id)
            )
            """,
            """
            CREATE TABLE llm_rate_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key_id INTEGER NOT NULL REFERENCES llm_api_keys(id) ON DELETE CASCADE,
                model_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                tokens INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            )
            """,
            "CREATE INDEX idx_llm_rate_usage ON llm_rate_usage(key_id, model_id, kind, created_at)",
            """
            CREATE TABLE llm_key_monthly_usage (
                key_id INTEGER NOT NULL REFERENCES llm_api_keys(id) ON DELETE CASCADE,
                month TEXT NOT NULL,
                requests INTEGER NOT NULL DEFAULT 0,
                tokens INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (key_id, month)
            )
            """,
            "CREATE TABLE llm_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        ],
    ),
]

#: Migrations that must run with ``PRAGMA foreign_keys = OFF`` (the pragma is a
#: no-op inside a transaction, so the runner toggles it around the transaction).
#: Needed for the SQLite "create new, copy, drop old, rename" table rebuild:
#: with foreign keys ON, dropping the old table cascades into its children.
FOREIGN_KEYS_OFF: frozenset[str] = frozenset({"0004_duplicates_publish_state"})
