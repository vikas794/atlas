"""Repository layer for the Atlas SQLite pipeline store."""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator

from backend.storage.artifacts import sha256_text
from backend.storage.cache import settings_hash as make_settings_hash
from backend.storage.database import connect, initialize, now_iso, transaction

STATUS_CREATED = "created"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_STALE = "stale"

_RUN_STATUSES = {STATUS_CREATED, STATUS_RUNNING, STATUS_SUCCEEDED, STATUS_FAILED, STATUS_STALE}
_ARTIFACT_STATUSES = {STATUS_CREATED, STATUS_RUNNING, STATUS_SUCCEEDED, STATUS_FAILED, STATUS_STALE}

_repository: RunRepository | None = None


def get_repository() -> RunRepository:
    """Return the process-wide shared repository (resolved from config)."""
    global _repository
    if _repository is None:
        from backend.storage.settings import get_settings

        settings = get_settings()
        _repository = RunRepository(settings["database_path"], settings["artifact_root"])
    return _repository


def reset_repository() -> None:
    global _repository
    _repository = None


def _state_hash(items: Iterable[tuple[str, str | None]]) -> str:
    """Stable hash of an ordered list of (key, value) pairs."""
    payload = json.dumps(sorted((key, value or "") for key, value in items), sort_keys=True)
    return sha256_text(payload)


class RunRepository:
    """All reads and short write transactions against ``data/atlas.sqlite3``.

    Every write method opens a short-lived connection inside an explicit
    transaction (WAL + busy timeout handle concurrent access). Artifact files
    are written by callers *before* the database record is committed.
    """

    def __init__(self, db_path: str | Path, artifact_root: str | Path):
        self.db_path = Path(db_path)
        self.artifact_root = Path(artifact_root)
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        initialize(self.db_path)

    def _conn(self):
        return connect(self.db_path)

    @contextmanager
    def _tx(self) -> Iterator:
        """Yield a write transaction and always close its connection."""
        conn = self._conn()
        try:
            with transaction(conn) as tx:
                yield tx
        finally:
            conn.close()

    # ------------------------------------------------------------------ runs

    def create_run(
        self,
        *,
        run_id: str,
        cache_key: str | None = None,
        search_query: str = "",
        normalized_query: str = "",
        max_videos: int = 4,
        transcript_language: str = "en",
        is_fallback: bool = False,
    ) -> str:
        now = now_iso()
        source_folder = str(self.artifact_root / run_id)
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO runs (run_id, cache_key, search_query, normalized_query,"
                " max_videos, transcript_language, status, created_at, updated_at,"
                " source_folder, is_fallback) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    cache_key,
                    search_query,
                    normalized_query,
                    max_videos,
                    transcript_language,
                    STATUS_CREATED,
                    now,
                    now,
                    source_folder,
                    1 if is_fallback else 0,
                ),
            )
        return run_id

    def get_run(self, run_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def list_runs(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM runs ORDER BY created_at DESC, run_id").fetchall()
        return [dict(row) for row in rows]

    def latest_run(self) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC, updated_at DESC, run_id LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def set_run_status(self, run_id: str, status: str, error: str | None = None) -> None:
        if status not in _RUN_STATUSES:
            raise ValueError(f"Invalid run status: {status}")
        now = now_iso()
        succeeded_at = now if status == STATUS_SUCCEEDED else None
        with self._tx() as conn:
            conn.execute(
                "UPDATE runs SET status = ?, updated_at = ?, error = ?, succeeded_at = ?"
                " WHERE run_id = ?",
                (status, now, error, succeeded_at, run_id),
            )

    # ----------------------------------------------------------------- videos

    def set_videos(self, run_id: str, videos: list[dict]) -> None:
        with self._tx() as conn:
            conn.execute("DELETE FROM videos WHERE run_id = ?", (run_id,))
            for position, video in enumerate(videos):
                conn.execute(
                    "INSERT INTO videos (run_id, video_id, position, data) VALUES (?,?,?,?)",
                    (run_id, video["video_id"], position, json.dumps(video, ensure_ascii=False)),
                )

    def get_videos(self, run_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM videos WHERE run_id = ? ORDER BY position, id", (run_id,)
            ).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def videos_state_hash(self, run_id: str) -> str:
        videos = self.get_videos(run_id)
        return _state_hash((v.get("video_id", ""), v.get("title", "")) for v in videos)

    # ------------------------------------------------------------ transcripts

    def upsert_transcripts(
        self,
        run_id: str,
        records: list[dict],
        settings: dict | None = None,
    ) -> None:
        input_hash = self.videos_state_hash(run_id)
        settings_key = make_settings_hash(settings or {})
        now = now_iso()
        with self._tx() as conn:
            for record in records:
                conn.execute(
                    "INSERT INTO transcripts (run_id, video_id, language, artifact_path,"
                    " content_hash, byte_size, status, input_hash, settings_hash, error,"
                    " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(run_id, video_id, language) DO UPDATE SET"
                    " artifact_path = excluded.artifact_path, content_hash = excluded.content_hash,"
                    " byte_size = excluded.byte_size, status = excluded.status,"
                    " input_hash = excluded.input_hash, settings_hash = excluded.settings_hash,"
                    " error = excluded.error, updated_at = excluded.updated_at",
                    (
                        run_id,
                        record["video_id"],
                        record.get("language", "en"),
                        record.get("artifact_path"),
                        record.get("content_hash"),
                        record.get("byte_size"),
                        record.get("status", STATUS_CREATED),
                        input_hash,
                        settings_key,
                        record.get("error"),
                        now,
                        now,
                    ),
                )
        self.recompute_run_hashes(run_id)

    def get_transcripts(self, run_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM transcripts WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def transcripts_state_hash(self, run_id: str) -> str:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT video_id, content_hash FROM transcripts"
                " WHERE run_id = ? AND status = ?",
                (run_id, STATUS_SUCCEEDED),
            ).fetchall()
        return _state_hash((row["video_id"], row["content_hash"]) for row in rows)

    # --------------------------------------------------------------- summaries

    def upsert_summaries(
        self,
        run_id: str,
        records: list[dict],
        settings: dict | None = None,
    ) -> None:
        input_hash = self.transcripts_state_hash(run_id)
        settings_key = make_settings_hash(settings or {})
        now = now_iso()
        with self._tx() as conn:
            for record in records:
                conn.execute(
                    "INSERT INTO summaries (run_id, video_id, artifact_path, content_hash,"
                    " byte_size, data, status, input_hash, settings_hash, error, created_at,"
                    " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(run_id, video_id) DO UPDATE SET"
                    " artifact_path = excluded.artifact_path, content_hash = excluded.content_hash,"
                    " byte_size = excluded.byte_size, data = excluded.data,"
                    " status = excluded.status, input_hash = excluded.input_hash,"
                    " settings_hash = excluded.settings_hash, error = excluded.error,"
                    " updated_at = excluded.updated_at",
                    (
                        run_id,
                        record["video_id"],
                        record.get("artifact_path"),
                        record.get("content_hash"),
                        record.get("byte_size"),
                        json.dumps(record.get("data", {}), ensure_ascii=False),
                        record.get("status", STATUS_CREATED),
                        input_hash,
                        settings_key,
                        record.get("error"),
                        now,
                        now,
                    ),
                )
        self.recompute_run_hashes(run_id)

    def get_summaries(self, run_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM summaries WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def summaries_state_hash(self, run_id: str) -> str:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT video_id, content_hash FROM summaries"
                " WHERE run_id = ? AND status = ?",
                (run_id, STATUS_SUCCEEDED),
            ).fetchall()
        return _state_hash((row["video_id"], row["content_hash"]) for row in rows)

    def _derived_input_hash(self, run_id: str) -> str:
        """Combined input identity for comparisons/assignments.

        Both depend on summaries and, transitively, the transcripts those summaries
        were built from — so refreshing either invalidates them.
        """
        run = self.get_run(run_id)
        return _state_hash(
            (
                ("summaries", run.get("summaries_hash") or ""),
                ("transcripts", run.get("transcripts_hash") or ""),
            )
        )

    def derived_input_hash(self, run_id: str) -> str:
        """Return the current input identity for comparisons and assignments."""
        return self._derived_input_hash(run_id)

    # ------------------------------------------------------------- comparisons

    def set_comparison(
        self,
        run_id: str,
        payload: dict,
        settings: dict | None = None,
        status: str = STATUS_SUCCEEDED,
        error: str | None = None,
    ) -> None:
        input_hash = self._derived_input_hash(run_id)
        settings_key = make_settings_hash(settings or {})
        now = now_iso()
        content = json.dumps(payload, ensure_ascii=False)
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO comparisons (run_id, data, content_hash, status, input_hash,"
                " settings_hash, error, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(run_id) DO UPDATE SET data = excluded.data,"
                " content_hash = excluded.content_hash, status = excluded.status,"
                " input_hash = excluded.input_hash, settings_hash = excluded.settings_hash,"
                " error = excluded.error, updated_at = excluded.updated_at",
                (
                    run_id,
                    content,
                    sha256_text(content),
                    status,
                    input_hash,
                    settings_key,
                    error,
                    now,
                    now,
                ),
            )

    def get_comparison(self, run_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM comparisons WHERE run_id = ?", (run_id,)
            ).fetchone()
        return dict(row) if row else None

    # -------------------------------------------------------------- assignments

    def upsert_assignments(
        self,
        run_id: str,
        records: list[dict],
        settings: dict | None = None,
    ) -> None:
        input_hash = self._derived_input_hash(run_id)
        settings_key = make_settings_hash(settings or {})
        now = now_iso()
        with self._tx() as conn:
            for record in records:
                conn.execute(
                    "INSERT INTO assignments (run_id, video_id, artifact_path, content_hash,"
                    " byte_size, metadata, display_metadata, status, input_hash, settings_hash,"
                    " error, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(run_id, video_id) DO UPDATE SET"
                    " artifact_path = excluded.artifact_path, content_hash = excluded.content_hash,"
                    " byte_size = excluded.byte_size, metadata = excluded.metadata,"
                    " display_metadata = excluded.display_metadata, status = excluded.status,"
                    " input_hash = excluded.input_hash, settings_hash = excluded.settings_hash,"
                    " error = excluded.error, updated_at = excluded.updated_at",
                    (
                        run_id,
                        record["video_id"],
                        record.get("artifact_path"),
                        record.get("content_hash"),
                        record.get("byte_size"),
                        json.dumps(record.get("metadata", {}), ensure_ascii=False),
                        json.dumps(record.get("display_metadata", {}), ensure_ascii=False),
                        record.get("status", STATUS_CREATED),
                        input_hash,
                        settings_key,
                        record.get("error"),
                        now,
                        now,
                    ),
                )

    def get_assignments(self, run_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM assignments WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    # ----------------------------------------------------------- generation jobs

    def start_job(self, run_id: str, kind: str, settings: dict | None = None) -> int:
        now = now_iso()
        settings_key = make_settings_hash(settings or {})
        with self._tx() as conn:
            cursor = conn.execute(
                "INSERT INTO generation_jobs (run_id, kind, status, settings_hash,"
                " created_at, started_at) VALUES (?,?,?,?,?,?)",
                (run_id, kind, STATUS_RUNNING, settings_key, now, now),
            )
            return int(cursor.lastrowid)

    def finish_job(self, run_id: str, kind: str) -> None:
        now = now_iso()
        with self._tx() as conn:
            conn.execute(
                "UPDATE generation_jobs SET status = ?, finished_at = ?"
                " WHERE run_id = ? AND kind = ? AND status = ?",
                (STATUS_SUCCEEDED, now, run_id, kind, STATUS_RUNNING),
            )

    def fail_job(self, run_id: str, kind: str, error: str | None = None) -> None:
        now = now_iso()
        with self._tx() as conn:
            conn.execute(
                "UPDATE generation_jobs SET status = ?, finished_at = ?, error = ?"
                " WHERE run_id = ? AND kind = ? AND status = ?",
                (STATUS_FAILED, now, error, run_id, kind, STATUS_RUNNING),
            )

    # ------------------------------------------------------------------ caching

    def put_cache_entry(
        self,
        cache_key: str,
        kind: str,
        run_id: str,
        normalized_query: str = "",
        settings: dict | None = None,
        ttl_days: int = 30,
        status: str = STATUS_SUCCEEDED,
    ) -> None:
        now = now_iso()
        expires = (datetime.now(UTC) + timedelta(days=ttl_days)).isoformat(timespec="seconds")
        settings_key = make_settings_hash(settings or {})
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO cache_entries (cache_key, kind, run_id, normalized_query,"
                " settings_hash, status, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT(cache_key) DO UPDATE SET kind = excluded.kind,"
                " run_id = excluded.run_id, normalized_query = excluded.normalized_query,"
                " settings_hash = excluded.settings_hash, status = excluded.status,"
                " created_at = excluded.created_at, expires_at = excluded.expires_at,"
                " hit_count = 0, last_hit_at = NULL",
                (
                    cache_key,
                    kind,
                    run_id,
                    normalized_query,
                    settings_key,
                    status,
                    now,
                    expires,
                ),
            )

    def get_cache_entry(self, cache_key: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM cache_entries WHERE cache_key = ?", (cache_key,)
            ).fetchone()
        return dict(row) if row else None

    def find_cached_run(self, cache_key: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT r.* FROM cache_entries c JOIN runs r ON r.run_id = c.run_id"
                " WHERE c.cache_key = ? AND c.kind = 'search' AND c.expires_at > ?"
                " AND r.status = ?"
                " ORDER BY r.created_at DESC, r.run_id LIMIT 1",
                (cache_key, now_iso(), STATUS_SUCCEEDED),
            ).fetchone()
        return dict(row) if row else None

    def touch_hit(self, cache_key: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE cache_entries SET hit_count = hit_count + 1, last_hit_at = ?"
                " WHERE cache_key = ?",
                (now_iso(), cache_key),
            )

    def list_cache_entries(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM cache_entries ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------- staleness

    def recompute_run_hashes(self, run_id: str) -> None:
        transcripts_hash = self.transcripts_state_hash(run_id)
        summaries_hash = self.summaries_state_hash(run_id)
        with self._tx() as conn:
            conn.execute(
                "UPDATE runs SET transcripts_hash = ?, summaries_hash = ?, updated_at = ?"
                " WHERE run_id = ?",
                (transcripts_hash, summaries_hash, now_iso(), run_id),
            )

    def mark_stale_derived(self, run_id: str) -> None:
        """Mark dependent artifacts stale when their input hashes no longer match.

        Summaries depend on the transcript state; comparisons and assignments
        depend on the summary state. Refreshing an upstream step invalidates
        everything derived from it.
        """
        run = self.get_run(run_id)
        if run is None:
            return
        now = now_iso()
        transcripts_hash = run.get("transcripts_hash")
        derived_hash = self._derived_input_hash(run_id)
        with self._tx() as conn:
            if transcripts_hash is not None:
                conn.execute(
                    "UPDATE summaries SET status = ?, updated_at = ?"
                    " WHERE run_id = ? AND status = ? AND input_hash IS NOT NULL"
                    " AND input_hash != ?",
                    (STATUS_STALE, now, run_id, STATUS_SUCCEEDED, transcripts_hash),
                )
            conn.execute(
                "UPDATE comparisons SET status = ?, updated_at = ?"
                " WHERE run_id = ? AND status = ? AND input_hash IS NOT NULL"
                " AND input_hash != ?",
                (STATUS_STALE, now, run_id, STATUS_SUCCEEDED, derived_hash),
            )
            conn.execute(
                "UPDATE assignments SET status = ?, updated_at = ?"
                " WHERE run_id = ? AND status = ? AND input_hash IS NOT NULL"
                " AND input_hash != ?",
                (STATUS_STALE, now, run_id, STATUS_SUCCEEDED, derived_hash),
            )

    # -------------------------------------------------------------- maintenance

    def purge_expired(self, retention_days: int = 90) -> dict:
        """Remove expired cache entries and, after retention, their runs/artifacts.

        Also deletes orphaned artifact folders (present on disk but not referenced
        by any run) that are older than the retention window.
        """
        now = datetime.now(UTC)
        retention_cutoff = now - timedelta(days=retention_days)
        retention_cutoff_iso = retention_cutoff.isoformat(timespec="seconds")

        removed_entries = 0
        removed_runs: list[str] = []
        with self._tx() as conn:
            expired = conn.execute(
                "SELECT cache_key, run_id FROM cache_entries WHERE expires_at <= ?",
                (now_iso(),),
            ).fetchall()
            removed_entries = len(expired)
            for row in expired:
                conn.execute("DELETE FROM cache_entries WHERE cache_key = ?", (row["cache_key"],))
            for row in expired:
                run = conn.execute(
                    "SELECT updated_at FROM runs WHERE run_id = ?", (row["run_id"],)
                ).fetchone()
                if run and run["updated_at"] <= retention_cutoff_iso:
                    conn.execute("DELETE FROM runs WHERE run_id = ?", (row["run_id"],))
                    removed_runs.append(row["run_id"])

        for run_id in removed_runs:
            folder = self.artifact_root / run_id
            if folder.exists():
                shutil.rmtree(folder, ignore_errors=True)

        known = {run["run_id"] for run in self.list_runs()}
        removed_orphans: list[str] = []
        if self.artifact_root.exists():
            for folder in self.artifact_root.iterdir():
                if not folder.is_dir() or folder.name in known:
                    continue
                if folder.stat().st_mtime <= retention_cutoff.timestamp():
                    shutil.rmtree(folder, ignore_errors=True)
                    removed_orphans.append(folder.name)

        return {
            "removed_cache_entries": removed_entries,
            "removed_runs": removed_runs,
            "removed_orphan_folders": removed_orphans,
        }

    def stats(self) -> dict:
        with self._conn() as conn:
            runs = conn.execute(
                "SELECT status, COUNT(*) AS count FROM runs GROUP BY status"
            ).fetchall()
            jobs = conn.execute(
                "SELECT status, COUNT(*) AS count FROM generation_jobs GROUP BY status"
            ).fetchall()
            cache = conn.execute(
                "SELECT COUNT(*) AS count FROM cache_entries"
            ).fetchone()["count"]
            expired = conn.execute(
                "SELECT COUNT(*) AS count FROM cache_entries WHERE expires_at <= ?",
                (now_iso(),),
            ).fetchone()["count"]
        return {
            "runs": {row["status"]: row["count"] for row in runs},
            "generation_jobs": {row["status"]: row["count"] for row in jobs},
            "cache_entries": cache,
            "expired_cache_entries": expired,
        }
