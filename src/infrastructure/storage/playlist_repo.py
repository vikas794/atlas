"""Repository over the ``playlists``/``videos``/``transcripts``/``summaries``
schema created by migration ``0003_playlist_pipeline`` (PRD section 7).

**Concurrency model.** SQLite is synchronous, so every public method here is
``async`` and runs its blocking work in a worker thread via
:func:`asyncio.to_thread`. Each of those blocking bodies opens its **own
short-lived connection** (read) or transaction (write) and closes it before
returning, so no connection is ever held across an ``await`` — that keeps the
event loop free while staying safe under concurrent pipeline tasks.

Every write sets ``updated_at``, and multi-statement writes run inside
:func:`src.infrastructure.storage.db.transaction` so they commit or roll back as
one unit. Nothing here deletes pipeline data implicitly: the only hard deletes
are :meth:`PlaylistRepository.delete_playlist` (which relies on
``ON DELETE CASCADE``) and the deliberate full-reprocess
:meth:`PlaylistRepository.reset_playlist`.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from src.domain.exceptions import StaleRunError
from src.domain.models.playlist import (
    FailureKind,
    FailureType,
    Playlist,
    Stage,
    Summary,
    Transcript,
    Video,
)
from src.domain.services.sync_diff import PlaylistEntry
from src.infrastructure.storage.db import connect, initialize, now_iso, transaction
from src.shared.text import sha256_text

#: SQLITE_MAX_VARIABLE_NUMBER is 999 on older builds; chunk `IN (...)` binds.
_IN_CLAUSE_CHUNK = 400

VideoInput = Video | PlaylistEntry


# --------------------------------------------------------------------- mappers


def _opt_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _to_stage(value: object) -> Stage:
    """Map a stored stage string to :class:`Stage`, defaulting to ``queued``."""
    try:
        return Stage(str(value))
    except ValueError:
        return Stage.QUEUED


def _to_failure_type(value: object) -> FailureType | None:
    if value is None or str(value) == "":
        return None
    try:
        return FailureType(str(value))
    except ValueError:
        return None


def _to_failure_kind(value: object) -> FailureKind | None:
    if value is None or str(value) == "":
        return None
    try:
        return FailureKind(str(value))
    except ValueError:
        return None


def _to_bool(value: object) -> bool:
    return bool(int(value or 0))


def _playlist_from_row(row: sqlite3.Row) -> Playlist:
    return Playlist(
        id=row["id"],
        playlist_id=row["playlist_id"],
        source_url=row["source_url"],
        title=row["title"],
        video_count=row["video_count"],
        status=row["status"],
        drive_folder_id=_opt_str(row["drive_folder_id"]),
        drive_index_doc_id=_opt_str(row["drive_index_doc_id"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_synced_at=_opt_str(row["last_synced_at"]),
        last_publish_error=_opt_str(row["last_publish_error"]),
        pad_width=int(row["pad_width"] or 0),
        index_digest=_opt_str(row["index_digest"]),
    )


def _video_from_row(row: sqlite3.Row) -> Video:
    return Video(
        id=row["id"],
        playlist_id=row["playlist_id"],
        youtube_video_id=row["youtube_video_id"],
        title=row["title"],
        position=row["position"],
        stage=_to_stage(row["stage"]),
        failure_type=_to_failure_type(row["failure_type"]),
        failure_kind=_to_failure_kind(row["failure_kind"]),
        failure_reason=_opt_str(row["failure_reason"]),
        retry_count=row["retry_count"],
        next_retry_at=_opt_str(row["next_retry_at"]),
        drive_doc_id=_opt_str(row["drive_doc_id"]),
        removed_upstream=_to_bool(row["removed_upstream"]),
        duplicate_of=row["duplicate_of_video_id"],
        run_token=int(row["run_token"] or 0),
        regen_requested=_to_bool(row["regen_requested"]),
        regen_prompt=_opt_str(row["regen_prompt"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _transcript_from_row(row: sqlite3.Row) -> Transcript:
    return Transcript(
        id=row["id"],
        video_id=row["video_id"],
        content=row["content"],
        language=row["language"],
        byte_size=row["byte_size"],
        content_hash=_opt_str(row["content_hash"]),
        fetched_at=row["fetched_at"],
        deleted_at=_opt_str(row["deleted_at"]),
        artifact_path=_opt_str(row["artifact_path"]),
    )


def _summary_from_row(row: sqlite3.Row) -> Summary:
    return Summary(
        id=row["id"],
        video_id=row["video_id"],
        content=row["content"],
        provider=_opt_str(row["provider"]),
        model=_opt_str(row["model"]),
        prompt_version=_opt_str(row["prompt_version"]),
        generated_at=row["generated_at"],
        edited=_to_bool(row["edited"]),
        approved=_to_bool(row["approved"]),
        approved_at=_opt_str(row["approved_at"]),
        published=_to_bool(row["published"]),
        published_at=_opt_str(row["published_at"]),
        published_digest=_opt_str(row["published_digest"]),
    )


def _fields(item: VideoInput) -> tuple[str, str, int]:
    """Normalize a ``Video`` or ``PlaylistEntry`` into insert columns."""
    return item.youtube_video_id, item.title, int(item.position)


def _chunks(values: Sequence[int]) -> Iterator[Sequence[int]]:
    for start in range(0, len(values), _IN_CLAUSE_CHUNK):
        yield values[start : start + _IN_CLAUSE_CHUNK]


# ----------------------------------------------------------------- repository


class PlaylistRepository:
    """Async-friendly access to the playlist pipeline tables."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        initialize(self.db_path)

    # ------------------------------------------------------------- plumbing

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        conn = connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = connect(self.db_path)
        try:
            with transaction(conn) as tx:
                yield tx
        finally:
            conn.close()

    # ------------------------------------------------------------- playlists

    async def create_playlist(
        self,
        playlist_id: str,
        source_url: str,
        title: str = "",
        *,
        status: str = "ingested",
        video_count: int = 0,
    ) -> Playlist:
        """Insert a new playlist row and return it.

        ``playlist_id`` is UNIQUE: re-ingesting a known URL must go through
        :meth:`get_playlist_by_youtube_id` and the incremental re-run (PRD 16).
        """
        return await asyncio.to_thread(
            self._create_playlist, playlist_id, source_url, title, status, video_count
        )

    def _create_playlist(
        self,
        playlist_id: str,
        source_url: str,
        title: str,
        status: str,
        video_count: int,
    ) -> Playlist:
        stamp = now_iso()
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO playlists (playlist_id, source_url, title, video_count,"
                " status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (playlist_id, source_url, title, video_count, status, stamp, stamp),
            )
            row = conn.execute(
                "SELECT * FROM playlists WHERE playlist_id = ?", (playlist_id,)
            ).fetchone()
        return _playlist_from_row(row)

    async def get_playlist(self, playlist_pk: int) -> Playlist | None:
        """Fetch a playlist by its internal primary key."""
        return await asyncio.to_thread(self._get_playlist, playlist_pk)

    def _get_playlist(self, playlist_pk: int) -> Playlist | None:
        with self._read() as conn:
            row = conn.execute("SELECT * FROM playlists WHERE id = ?", (playlist_pk,)).fetchone()
        return _playlist_from_row(row) if row is not None else None

    async def get_playlist_by_youtube_id(self, playlist_id: str) -> Playlist | None:
        """Fetch a playlist by its YouTube playlist ID (the UNIQUE column)."""
        return await asyncio.to_thread(self._get_playlist_by_youtube_id, playlist_id)

    def _get_playlist_by_youtube_id(self, playlist_id: str) -> Playlist | None:
        with self._read() as conn:
            row = conn.execute(
                "SELECT * FROM playlists WHERE playlist_id = ?", (playlist_id,)
            ).fetchone()
        return _playlist_from_row(row) if row is not None else None

    async def list_playlists(
        self,
        *,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Playlist]:
        """List playlists newest-first, optionally filtered by status."""
        return await asyncio.to_thread(self._list_playlists, status, limit, offset)

    def _list_playlists(
        self, status: str | None, limit: int | None, offset: int
    ) -> list[Playlist]:
        sql = "SELECT * FROM playlists"
        params: list[object] = []
        if status is not None:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC, id DESC"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        elif offset:
            sql += " LIMIT -1 OFFSET ?"
            params.append(offset)
        with self._read() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_playlist_from_row(row) for row in rows]

    async def count_playlists(self, *, status: str | None = None) -> int:
        """Total number of playlists (optionally filtered by status), ignoring paging."""
        return await asyncio.to_thread(self._count_playlists, status)

    def _count_playlists(self, status: str | None) -> int:
        sql = "SELECT COUNT(*) AS total FROM playlists"
        params: list[object] = []
        if status is not None:
            sql += " WHERE status = ?"
            params.append(status)
        with self._read() as conn:
            row = conn.execute(sql, params).fetchone()
        return int(row["total"])

    async def update_playlist(
        self,
        playlist_pk: int,
        *,
        title: str | None = None,
        status: str | None = None,
        video_count: int | None = None,
        source_url: str | None = None,
    ) -> Playlist | None:
        """Patch mutable playlist columns; ``None`` arguments are left untouched."""
        return await asyncio.to_thread(
            self._update_playlist, playlist_pk, title, status, video_count, source_url
        )

    def _update_playlist(
        self,
        playlist_pk: int,
        title: str | None,
        status: str | None,
        video_count: int | None,
        source_url: str | None,
    ) -> Playlist | None:
        sets: list[str] = []
        params: list[object] = []
        for column, value in (
            ("title", title),
            ("status", status),
            ("video_count", video_count),
            ("source_url", source_url),
        ):
            if value is not None:
                sets.append(f"{column} = ?")
                params.append(value)
        if not sets:
            return self._get_playlist(playlist_pk)
        sets.append("updated_at = ?")
        params.append(now_iso())
        params.append(playlist_pk)
        with self._tx() as conn:
            conn.execute(f"UPDATE playlists SET {', '.join(sets)} WHERE id = ?", params)
            row = conn.execute("SELECT * FROM playlists WHERE id = ?", (playlist_pk,)).fetchone()
        return _playlist_from_row(row) if row is not None else None

    async def begin_publish(self, playlist_pk: int) -> bool:
        """Atomically mark the playlist ``publishing``; False if it already is (or is missing)."""
        return await asyncio.to_thread(self._begin_publish, playlist_pk)

    def _begin_publish(self, playlist_pk: int) -> bool:
        with self._tx() as conn:
            cursor = conn.execute(
                "UPDATE playlists SET status = 'publishing', last_publish_error = NULL,"
                " updated_at = ? WHERE id = ? AND status != 'publishing'",
                (now_iso(), playlist_pk),
            )
            return cursor.rowcount > 0

    async def finish_publish(self, playlist_pk: int, error: str | None = None) -> None:
        """Record the outcome: ``published`` on success, ``publish_failed`` + reason otherwise."""
        await asyncio.to_thread(self._finish_publish, playlist_pk, error)

    def _finish_publish(self, playlist_pk: int, error: str | None) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE playlists SET status = ?, last_publish_error = ?, updated_at = ?"
                " WHERE id = ?",
                ("publish_failed" if error else "published", error, now_iso(), playlist_pk),
            )

    async def list_publishing_playlists(self) -> list[int]:
        """Primary keys of playlists left in ``publishing`` (crash recovery re-enqueues them)."""
        return await asyncio.to_thread(self._list_publishing_playlists)

    def _list_publishing_playlists(self) -> list[int]:
        with self._read() as conn:
            rows = conn.execute(
                "SELECT id FROM playlists WHERE status = 'publishing' ORDER BY id"
            ).fetchall()
        return [int(row["id"]) for row in rows]

    async def set_pad_width(self, playlist_pk: int, width: int) -> None:
        """Remember the zero-pad width used by published Doc names."""
        await asyncio.to_thread(self._set_playlist_column, playlist_pk, "pad_width", width)

    async def set_index_digest(self, playlist_pk: int, digest: str | None) -> None:
        """Remember the hash of the index Doc text last written to Drive."""
        await asyncio.to_thread(self._set_playlist_column, playlist_pk, "index_digest", digest)

    def _set_playlist_column(self, playlist_pk: int, column: str, value: object) -> None:
        if column not in {"pad_width", "index_digest"}:
            raise ValueError(f"Unsupported playlist column: {column}")
        with self._tx() as conn:
            conn.execute(
                f"UPDATE playlists SET {column} = ?, updated_at = ? WHERE id = ?",
                (value, now_iso(), playlist_pk),
            )

    async def touch_synced(self, playlist_pk: int, synced_at: str | None = None) -> None:
        """Stamp ``last_synced_at`` (defaults to now) after a re-run."""
        await asyncio.to_thread(self._touch_synced, playlist_pk, synced_at)

    def _touch_synced(self, playlist_pk: int, synced_at: str | None) -> None:
        stamp = now_iso()
        with self._tx() as conn:
            conn.execute(
                "UPDATE playlists SET last_synced_at = ?, updated_at = ? WHERE id = ?",
                (synced_at or stamp, stamp, playlist_pk),
            )

    async def delete_playlist(self, playlist_pk: int) -> bool:
        """Delete a playlist; videos, transcripts and summaries cascade away."""
        return await asyncio.to_thread(self._delete_playlist, playlist_pk)

    def _delete_playlist(self, playlist_pk: int) -> bool:
        with self._tx() as conn:
            cursor = conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_pk,))
            return cursor.rowcount > 0

    async def set_drive_folder(self, playlist_pk: int, folder_id: str | None) -> None:
        """Store the Drive folder ID for this playlist (PRD section 19)."""
        await asyncio.to_thread(self._set_drive_folder, playlist_pk, folder_id)

    def _set_drive_folder(self, playlist_pk: int, folder_id: str | None) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE playlists SET drive_folder_id = ?, updated_at = ? WHERE id = ?",
                (folder_id, now_iso(), playlist_pk),
            )

    async def set_drive_index_doc(self, playlist_pk: int, doc_id: str | None) -> None:
        """Store the Drive index Doc ID for this playlist (PRD section 19)."""
        await asyncio.to_thread(self._set_drive_index_doc, playlist_pk, doc_id)

    def _set_drive_index_doc(self, playlist_pk: int, doc_id: str | None) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE playlists SET drive_index_doc_id = ?, updated_at = ? WHERE id = ?",
                (doc_id, now_iso(), playlist_pk),
            )

    # ---------------------------------------------------------------- videos

    async def insert_videos(self, playlist_pk: int, videos: Iterable[VideoInput]) -> list[Video]:
        """Insert one row per playlist position, in the order given.

        A video that already has a row in this playlist (or appears earlier in
        ``videos``) is stored again at its own position with ``duplicate_of`` set
        to the first occurrence: it is numbered like any other video but is
        processed once and reuses the first summary (PRD section 20).

        Positions must be new: ``UNIQUE (playlist_id, position)`` rejects a
        position that is already stored, so callers diff first
        (:func:`~src.domain.services.sync_diff.diff_playlist`). Returns the
        inserted videos in order.
        """
        return await asyncio.to_thread(self._insert_videos, playlist_pk, list(videos))

    def _insert_videos(self, playlist_pk: int, videos: list[VideoInput]) -> list[Video]:
        rows = [_fields(video) for video in videos]
        if not rows:
            return []
        stamp = now_iso()
        new_ids: list[int] = []
        with self._tx() as conn:
            first_of: dict[str, int] = {
                found["youtube_video_id"]: int(found["id"])
                for found in conn.execute(
                    "SELECT id, youtube_video_id FROM videos"
                    " WHERE playlist_id = ? AND duplicate_of_video_id IS NULL"
                    " ORDER BY position",
                    (playlist_pk,),
                )
            }
            for video_id, title, position in rows:
                duplicate_of = first_of.get(video_id)
                cursor = conn.execute(
                    "INSERT INTO videos (playlist_id, youtube_video_id, title, position, stage,"
                    " duplicate_of_video_id, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        playlist_pk,
                        video_id,
                        title,
                        position,
                        Stage.QUEUED.value,
                        duplicate_of,
                        stamp,
                        stamp,
                    ),
                )
                new_id = int(cursor.lastrowid)
                if duplicate_of is None:
                    first_of[video_id] = new_id
                new_ids.append(new_id)
        stored: list[Video] = []
        with self._read() as conn:
            for chunk in _chunks(new_ids):
                placeholders = ",".join("?" * len(chunk))
                found_rows = conn.execute(
                    f"SELECT * FROM videos WHERE id IN ({placeholders}) ORDER BY position",
                    tuple(chunk),
                ).fetchall()
                stored.extend(_video_from_row(row) for row in found_rows)
        return stored

    async def get_video(self, video_pk: int) -> Video | None:
        """Fetch a single video by primary key."""
        return await asyncio.to_thread(self._get_video, video_pk)

    def _get_video(self, video_pk: int) -> Video | None:
        with self._read() as conn:
            row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_pk,)).fetchone()
        return _video_from_row(row) if row is not None else None

    async def list_videos(
        self,
        playlist_pk: int,
        stage: Stage | None = None,
        search: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[Video]:
        """List a playlist's videos in playlist order, filterable by stage/title."""
        return await asyncio.to_thread(
            self._list_videos, playlist_pk, stage, search, limit, offset
        )

    def _list_videos(
        self,
        playlist_pk: int,
        stage: Stage | None,
        search: str | None,
        limit: int | None,
        offset: int | None,
    ) -> list[Video]:
        sql = "SELECT * FROM videos WHERE playlist_id = ?"
        params: list[object] = [playlist_pk]
        if stage is not None:
            sql += " AND stage = ?"
            params.append(Stage(stage).value)
        if search:
            sql += " AND (title LIKE ? OR youtube_video_id LIKE ?)"
            pattern = f"%{search}%"
            params.extend([pattern, pattern])
        sql += " ORDER BY position, id"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
            if offset:
                sql += " OFFSET ?"
                params.append(offset)
        elif offset:
            sql += " LIMIT -1 OFFSET ?"
            params.append(offset)
        with self._read() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_video_from_row(row) for row in rows]

    async def count_videos(
        self,
        playlist_pk: int,
        stage: Stage | None = None,
        search: str | None = None,
    ) -> int:
        """Number of videos matching the filters, ignoring paging (the true total)."""
        return await asyncio.to_thread(self._count_videos, playlist_pk, stage, search)

    def _count_videos(self, playlist_pk: int, stage: Stage | None, search: str | None) -> int:
        sql = "SELECT COUNT(*) AS total FROM videos WHERE playlist_id = ?"
        params: list[object] = [playlist_pk]
        if stage is not None:
            sql += " AND stage = ?"
            params.append(Stage(stage).value)
        if search:
            sql += " AND (title LIKE ? OR youtube_video_id LIKE ?)"
            pattern = f"%{search}%"
            params.extend([pattern, pattern])
        with self._read() as conn:
            row = conn.execute(sql, params).fetchone()
        return int(row["total"])

    async def max_position(self, playlist_pk: int) -> int:
        """Largest stored 1-based position in the playlist (0 when it has no videos)."""
        return await asyncio.to_thread(self._max_position, playlist_pk)

    def _max_position(self, playlist_pk: int) -> int:
        with self._read() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(position), 0) AS top FROM videos WHERE playlist_id = ?",
                (playlist_pk,),
            ).fetchone()
        return int(row["top"])

    async def update_stage(
        self,
        video_pk: int,
        stage: Stage,
        *,
        failure_type: FailureType | None = None,
        failure_kind: FailureKind | None = None,
        failure_reason: str | None = None,
        next_retry_at: str | None = None,
        token: int | None = None,
        reset_retries: bool = False,
    ) -> Video | None:
        """Persist a stage move (PRD section 15: before *and* after the side effect).

        Failure columns are written as given, so passing no failure clears the
        previous one -- a successful stage advance should never keep a stale reason.

        Args:
            token: The video's ``run_token`` when the caller started. If the video
                was reset, retried or deleted since, nothing is written and
                :class:`~src.domain.exceptions.StaleRunError` is raised.
            reset_retries: Also reset ``retry_count`` to 0 -- a phase completed, so the
                next phase starts with the full automatic-retry budget.
        """
        return await asyncio.to_thread(
            self._update_stage,
            video_pk,
            stage,
            failure_type,
            failure_kind,
            failure_reason,
            next_retry_at,
            token,
            reset_retries,
        )

    def _update_stage(
        self,
        video_pk: int,
        stage: Stage,
        failure_type: FailureType | None,
        failure_kind: FailureKind | None,
        failure_reason: str | None,
        next_retry_at: str | None,
        token: int | None,
        reset_retries: bool = False,
    ) -> Video | None:
        sql = (
            "UPDATE videos SET stage = ?, failure_type = ?, failure_kind = ?,"
            " failure_reason = ?, next_retry_at = ?,"
            + (" retry_count = 0," if reset_retries else "")
            + " updated_at = ? WHERE id = ?"
        )
        params: list[object] = [
            Stage(stage).value,
            failure_type.value if failure_type is not None else None,
            failure_kind.value if failure_kind is not None else None,
            failure_reason,
            next_retry_at,
            now_iso(),
            video_pk,
        ]
        if token is not None:
            sql += " AND run_token = ?"
            params.append(token)
        with self._tx() as conn:
            cursor = conn.execute(sql, params)
            if cursor.rowcount == 0 and token is not None:
                raise StaleRunError(f"Video {video_pk} was reset or deleted while running")
            row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_pk,)).fetchone()
        return _video_from_row(row) if row is not None else None

    async def clear_failure(self, video_pk: int) -> None:
        """Drop failure columns and reset the retry budget after a manual retry."""
        await asyncio.to_thread(self._clear_failure, video_pk)

    def _clear_failure(self, video_pk: int) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE videos SET failure_type = NULL, failure_kind = NULL,"
                " failure_reason = NULL, retry_count = 0, next_retry_at = NULL,"
                " run_token = run_token + 1, updated_at = ? WHERE id = ?",
                (now_iso(), video_pk),
            )

    async def increment_retry(
        self, video_pk: int, *, next_retry_at: str | None = None, token: int | None = None
    ) -> int:
        """Bump ``retry_count``, schedule the next attempt, return the new count.

        ``token`` is the stale-run guard described on :meth:`update_stage`: a manual
        retry or reprocess that happened meanwhile must not get a stale schedule.
        """
        return await asyncio.to_thread(self._increment_retry, video_pk, next_retry_at, token)

    def _increment_retry(self, video_pk: int, next_retry_at: str | None, token: int | None) -> int:
        with self._tx() as conn:
            self._check_token(conn, video_pk, token)
            conn.execute(
                "UPDATE videos SET retry_count = retry_count + 1, next_retry_at = ?,"
                " updated_at = ? WHERE id = ?",
                (next_retry_at, now_iso(), video_pk),
            )
            row = conn.execute(
                "SELECT retry_count FROM videos WHERE id = ?", (video_pk,)
            ).fetchone()
        return int(row["retry_count"]) if row is not None else 0

    async def set_drive_doc_id(self, video_pk: int, doc_id: str | None) -> None:
        """Store the Drive Doc ID immediately after creating/updating the Doc."""
        await asyncio.to_thread(self._set_drive_doc_id, video_pk, doc_id)

    def _set_drive_doc_id(self, video_pk: int, doc_id: str | None) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE videos SET drive_doc_id = ?, updated_at = ? WHERE id = ?",
                (doc_id, now_iso(), video_pk),
            )

    async def flag_removed_upstream(self, ids: Iterable[int]) -> int:
        """Flag videos by primary key as gone from YouTube; nothing is deleted."""
        return await asyncio.to_thread(self._flag_removed_upstream, list(ids))

    def _flag_removed_upstream(self, ids: list[int]) -> int:
        if not ids:
            return 0
        changed = 0
        with self._tx() as conn:
            for chunk in _chunks(ids):
                placeholders = ",".join("?" * len(chunk))
                cursor = conn.execute(
                    f"UPDATE videos SET removed_upstream = 1, updated_at = ?"
                    f" WHERE id IN ({placeholders}) AND removed_upstream = 0",
                    (now_iso(), *chunk),
                )
                changed += cursor.rowcount
        return changed

    async def clear_removed_upstream(self, ids: Iterable[int]) -> int:
        """Clear the "no longer in playlist" flag of videos that reappeared upstream."""
        return await asyncio.to_thread(self._clear_removed_upstream, list(ids))

    def _clear_removed_upstream(self, ids: list[int]) -> int:
        if not ids:
            return 0
        changed = 0
        with self._tx() as conn:
            for chunk in _chunks(ids):
                placeholders = ",".join("?" * len(chunk))
                cursor = conn.execute(
                    f"UPDATE videos SET removed_upstream = 0, updated_at = ?"
                    f" WHERE id IN ({placeholders}) AND removed_upstream = 1",
                    (now_iso(), *chunk),
                )
                changed += cursor.rowcount
        return changed

    async def request_regenerate(self, video_pk: int, prompt: str | None = None) -> bool:
        """Queue a "regenerate notes" run: re-queue the video with the regenerate flag.

        The stored summary and transcript are kept until the new notes arrive; the
        worker reuses the transcript or re-fetches it if it was cleaned up. ``prompt`` is
        a one-off prompt template for this run only: it is kept on the video until the new
        notes are stored (so automatic retries reuse it) and then cleared.
        """
        return await asyncio.to_thread(self._request_regenerate, video_pk, prompt)

    def _request_regenerate(self, video_pk: int, prompt: str | None) -> bool:
        with self._tx() as conn:
            cursor = conn.execute(
                "UPDATE videos SET stage = ?, regen_requested = 1, regen_prompt = ?,"
                " failure_type = NULL,"
                " failure_kind = NULL, failure_reason = NULL, retry_count = 0,"
                " next_retry_at = NULL, run_token = run_token + 1, updated_at = ?"
                " WHERE id = ?",
                (Stage.QUEUED.value, prompt, now_iso(), video_pk),
            )
            return cursor.rowcount > 0

    async def count_by_stage(self, playlist_pk: int) -> dict[str, int]:
        """Per-stage counts for the playlist progress strip; missing stages are 0."""
        return await asyncio.to_thread(self._count_by_stage, playlist_pk)

    def _count_by_stage(self, playlist_pk: int) -> dict[str, int]:
        counts = {stage.value: 0 for stage in Stage}
        with self._read() as conn:
            rows = conn.execute(
                "SELECT stage, COUNT(*) AS total FROM videos WHERE playlist_id = ?"
                " GROUP BY stage",
                (playlist_pk,),
            ).fetchall()
        for row in rows:
            counts[_to_stage(row["stage"]).value] = int(row["total"])
        return counts

    async def reset_playlist(self, playlist_pk: int) -> None:
        """Full reprocess: drop transcripts/summaries and re-queue every video.

        Video rows are kept (positions, titles and published Doc numbering stay
        stable), but transcripts and summaries are hard-deleted because a full
        reprocess explicitly discards previously generated work.
        """
        await asyncio.to_thread(self._reset_playlist, playlist_pk)

    def _reset_playlist(self, playlist_pk: int) -> None:
        # drive_doc_id and the playlist's folder / index ids are deliberately KEPT:
        # re-publishing after a reprocess must update the existing Docs in place,
        # not orphan them and create duplicates (PRD section 15, 19).
        with self._tx() as conn:
            conn.execute(
                "DELETE FROM summaries WHERE video_id IN"
                " (SELECT id FROM videos WHERE playlist_id = ?)",
                (playlist_pk,),
            )
            conn.execute(
                "DELETE FROM transcripts WHERE video_id IN"
                " (SELECT id FROM videos WHERE playlist_id = ?)",
                (playlist_pk,),
            )
            conn.execute(
                "UPDATE videos SET stage = ?, failure_type = NULL, failure_kind = NULL,"
                " failure_reason = NULL, retry_count = 0, next_retry_at = NULL,"
                " regen_requested = 0, regen_prompt = NULL, run_token = run_token + 1,"
                " removed_upstream = 0,"
                " updated_at = ? WHERE playlist_id = ?",
                (Stage.QUEUED.value, now_iso(), playlist_pk),
            )
            conn.execute(
                "UPDATE playlists SET status = CASE WHEN status = 'publishing' THEN status"
                " ELSE 'ingested' END, last_publish_error = NULL, updated_at = ? WHERE id = ?",
                (now_iso(), playlist_pk),
            )

    async def list_queued_videos(
        self,
        playlist_pk: int | None = None,
        limit: int | None = None,
        stages: tuple[Stage, ...] = (Stage.QUEUED, Stage.TRANSCRIPT_READY),
    ) -> list[Video]:
        """Videos the worker may run now, in playlist order.

        ``stages`` selects the pool: ``queued`` videos need a transcript fetch,
        ``transcript_ready`` videos are waiting for notes generation. A duplicate row
        (PRD section 20) only becomes runnable once its first occurrence has a
        summary to reuse -- or has failed for good (no automatic retry pending), so
        the duplicate can record that failure instead of sitting queued forever.
        """
        return await asyncio.to_thread(self._list_queued_videos, playlist_pk, limit, stages)

    def _list_queued_videos(
        self, playlist_pk: int | None, limit: int | None, stages: tuple[Stage, ...]
    ) -> list[Video]:
        marks = ", ".join("?" for _ in stages)
        sql = (
            f"SELECT v.* FROM videos v WHERE v.stage IN ({marks}) AND v.removed_upstream = 0"
            " AND (v.duplicate_of_video_id IS NULL OR EXISTS ("
            "   SELECT 1 FROM videos o WHERE o.id = v.duplicate_of_video_id"
            "   AND (o.stage IN (?, ?, ?) OR (o.stage = ? AND o.next_retry_at IS NULL))))"
        )
        params: list[object] = [
            *(s.value for s in stages),
            Stage.PENDING_REVIEW.value,
            Stage.APPROVED.value,
            Stage.PUBLISHED.value,
            Stage.FAILED.value,
        ]
        if playlist_pk is not None:
            sql += " AND v.playlist_id = ?"
            params.append(playlist_pk)
        sql += " ORDER BY v.playlist_id, v.position, v.id"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._read() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_video_from_row(row) for row in rows]

    async def list_transient_due(
        self,
        playlist_pk: int | None = None,
        now_iso_str: str | None = None,
        limit: int | None = None,
    ) -> list[Video]:
        """Transiently failed videos whose scheduled ``next_retry_at`` has arrived.

        A transient failure with ``next_retry_at IS NULL`` has *exhausted* its
        automatic retries and is manual-retry only: it must never be picked up
        again here, or the worker would retry it forever and burn quota.
        """
        stamp = now_iso_str or now_iso()
        return await asyncio.to_thread(self._list_transient_due, playlist_pk, stamp, limit)

    def _list_transient_due(
        self, playlist_pk: int | None, now: str, limit: int | None
    ) -> list[Video]:
        sql = (
            "SELECT * FROM videos WHERE stage = ? AND failure_kind = ?"
            " AND removed_upstream = 0"
            " AND next_retry_at IS NOT NULL AND next_retry_at <= ?"
        )
        params: list[object] = [Stage.FAILED.value, FailureKind.TRANSIENT.value, now]
        if playlist_pk is not None:
            sql += " AND playlist_id = ?"
            params.append(playlist_pk)
        sql += " ORDER BY next_retry_at, position, id"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._read() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_video_from_row(row) for row in rows]

    async def recover_in_flight_videos(self) -> int:
        """Crash recovery (PRD section 15): re-queue every video left mid-stage.

        ``fetching_transcript``, ``transcript_ready`` and ``generating_notes`` are
        reset to ``queued``; a stored transcript is reused, so work already done is
        never repeated.
        """
        return await asyncio.to_thread(self._recover_in_flight_videos)

    def _recover_in_flight_videos(self) -> int:
        with self._tx() as conn:
            cursor = conn.execute(
                "UPDATE videos SET stage = ?, updated_at = ? WHERE stage IN (?, ?, ?)",
                (
                    Stage.QUEUED.value,
                    now_iso(),
                    Stage.FETCHING_TRANSCRIPT.value,
                    Stage.TRANSCRIPT_READY.value,
                    Stage.GENERATING_NOTES.value,
                ),
            )
            return cursor.rowcount

    # ----------------------------------------------------------- transcripts

    async def upsert_transcript(
        self,
        video_pk: int,
        content: str,
        *,
        language: str = "en",
        fetched_at: str | None = None,
        content_hash: str | None = None,
        byte_size: int | None = None,
        artifact_path: str | None = None,
        token: int | None = None,
    ) -> Transcript:
        """Store or replace a transcript, clearing any soft-delete marker.

        ``token`` is the stale-run guard described on :meth:`update_stage`.
        """
        return await asyncio.to_thread(
            self._upsert_transcript,
            video_pk,
            content,
            language,
            fetched_at,
            content_hash,
            byte_size,
            artifact_path,
            token,
        )

    def _upsert_transcript(
        self,
        video_pk: int,
        content: str,
        language: str,
        fetched_at: str | None,
        content_hash: str | None,
        byte_size: int | None,
        artifact_path: str | None,
        token: int | None,
    ) -> Transcript:
        digest = content_hash if content_hash is not None else sha256_text(content)
        size = byte_size if byte_size is not None else len(content.encode("utf-8"))
        with self._tx() as conn:
            self._check_token(conn, video_pk, token)
            conn.execute(
                "INSERT INTO transcripts (video_id, content, language, byte_size,"
                " content_hash, fetched_at, deleted_at, artifact_path)"
                " VALUES (?, ?, ?, ?, ?, ?, NULL, ?)"
                " ON CONFLICT (video_id) DO UPDATE SET content = excluded.content,"
                " language = excluded.language, byte_size = excluded.byte_size,"
                " content_hash = excluded.content_hash, fetched_at = excluded.fetched_at,"
                " deleted_at = NULL, artifact_path = excluded.artifact_path",
                (video_pk, content, language, size, digest, fetched_at or now_iso(),
                 artifact_path or None),
            )
            row = conn.execute(
                "SELECT * FROM transcripts WHERE video_id = ?", (video_pk,)
            ).fetchone()
        return _transcript_from_row(row)

    @staticmethod
    def _check_token(conn: sqlite3.Connection, video_pk: int, token: int | None) -> None:
        """Raise StaleRunError when the video vanished or its run token moved on."""
        if token is None:
            return
        row = conn.execute("SELECT run_token FROM videos WHERE id = ?", (video_pk,)).fetchone()
        if row is None or int(row["run_token"]) != token:
            raise StaleRunError(f"Video {video_pk} was reset or deleted while running")

    async def get_transcript(self, video_pk: int) -> Transcript | None:
        """Fetch the live transcript, or ``None`` when soft-deleted/missing."""
        return await asyncio.to_thread(self._get_transcript, video_pk)

    def _get_transcript(self, video_pk: int) -> Transcript | None:
        with self._read() as conn:
            row = conn.execute(
                "SELECT * FROM transcripts WHERE video_id = ? AND deleted_at IS NULL",
                (video_pk,),
            ).fetchone()
        return _transcript_from_row(row) if row is not None else None

    async def get_transcript_artifact_paths(
        self, *, video_pk: int | None = None, playlist_pk: int | None = None
    ) -> list[str]:
        """Raw subtitle file paths of live transcripts for one video or a whole playlist."""
        return await asyncio.to_thread(self._get_transcript_artifact_paths, video_pk, playlist_pk)

    def _get_transcript_artifact_paths(
        self, video_pk: int | None, playlist_pk: int | None
    ) -> list[str]:
        sql = (
            "SELECT t.artifact_path FROM transcripts t JOIN videos v ON v.id = t.video_id"
            " WHERE t.artifact_path IS NOT NULL"
        )
        params: list[object] = []
        if video_pk is not None:
            sql += " AND t.video_id = ?"
            params.append(video_pk)
        if playlist_pk is not None:
            sql += " AND v.playlist_id = ?"
            params.append(playlist_pk)
        with self._read() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [str(row["artifact_path"]) for row in rows]

    async def soft_delete_transcript(self, video_pk: int, deleted_at: str | None = None) -> bool:
        """Clear a transcript's text but keep its audit row (PRD section 7, FR11).

        ``content`` is emptied and ``artifact_path`` dropped; ``deleted_at``,
        ``byte_size``, ``content_hash`` and ``language`` stay as the audit trail.
        Returns True when a live transcript was cleared.
        """
        return await asyncio.to_thread(self._soft_delete_transcript, video_pk, deleted_at)

    def _soft_delete_transcript(self, video_pk: int, deleted_at: str | None) -> bool:
        with self._tx() as conn:
            cursor = conn.execute(
                "UPDATE transcripts SET deleted_at = ?, content = '', artifact_path = NULL"
                " WHERE video_id = ? AND deleted_at IS NULL",
                (deleted_at or now_iso(), video_pk),
            )
            return cursor.rowcount > 0

    async def soft_delete_all_transcripts(
        self, playlist_pk: int, deleted_at: str | None = None
    ) -> int:
        """Soft-delete (clear) every live transcript of a playlist; returns the row count."""
        return await asyncio.to_thread(self._soft_delete_all_transcripts, playlist_pk, deleted_at)

    def _soft_delete_all_transcripts(self, playlist_pk: int, deleted_at: str | None) -> int:
        with self._tx() as conn:
            cursor = conn.execute(
                "UPDATE transcripts SET deleted_at = ?, content = '', artifact_path = NULL"
                " WHERE deleted_at IS NULL"
                " AND video_id IN (SELECT id FROM videos WHERE playlist_id = ?)",
                (deleted_at or now_iso(), playlist_pk),
            )
            return cursor.rowcount

    # -------------------------------------------------------------- summaries

    async def upsert_summary(
        self,
        video_pk: int,
        content: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        prompt_version: str | None = None,
        generated_at: str | None = None,
        token: int | None = None,
    ) -> Summary:
        """Store or replace generated notes, preserving review/publish flags.

        Replacing content without going through :meth:`regenerate` is a first-write
        only path; use ``regenerate`` when discarding previously approved work.
        ``token`` is the stale-run guard described on :meth:`update_stage`.
        """
        return await asyncio.to_thread(
            self._upsert_summary,
            video_pk,
            content,
            provider,
            model,
            prompt_version,
            generated_at,
            token,
        )

    def _upsert_summary(
        self,
        video_pk: int,
        content: str,
        provider: str | None,
        model: str | None,
        prompt_version: str | None,
        generated_at: str | None,
        token: int | None,
    ) -> Summary:
        with self._tx() as conn:
            self._check_token(conn, video_pk, token)
            conn.execute(
                "INSERT INTO summaries (video_id, content, provider, model, prompt_version,"
                " generated_at) VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (video_id) DO UPDATE SET content = excluded.content,"
                " provider = excluded.provider, model = excluded.model,"
                " prompt_version = excluded.prompt_version,"
                " generated_at = excluded.generated_at",
                (video_pk, content, provider, model, prompt_version, generated_at or now_iso()),
            )
            row = conn.execute("SELECT * FROM summaries WHERE video_id = ?", (video_pk,)).fetchone()
        return _summary_from_row(row)

    async def copy_summary(
        self, source_video_pk: int, target_video_pk: int, *, token: int | None = None
    ) -> Summary | None:
        """Give a duplicate video its own copy of the first occurrence's summary.

        The copy starts unapproved and unpublished (its own review/publish life
        cycle). Returns None when the source has no summary.
        """
        return await asyncio.to_thread(
            self._copy_summary, source_video_pk, target_video_pk, token
        )

    def _copy_summary(
        self, source_pk: int, target_pk: int, token: int | None
    ) -> Summary | None:
        with self._tx() as conn:
            self._check_token(conn, target_pk, token)
            source = conn.execute(
                "SELECT * FROM summaries WHERE video_id = ?", (source_pk,)
            ).fetchone()
            if source is None:
                return None
            conn.execute(
                "INSERT INTO summaries (video_id, content, provider, model, prompt_version,"
                " generated_at) VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (video_id) DO UPDATE SET content = excluded.content,"
                " provider = excluded.provider, model = excluded.model,"
                " prompt_version = excluded.prompt_version,"
                " generated_at = excluded.generated_at",
                (
                    target_pk,
                    source["content"],
                    source["provider"],
                    source["model"],
                    source["prompt_version"],
                    now_iso(),
                ),
            )
            row = conn.execute(
                "SELECT * FROM summaries WHERE video_id = ?", (target_pk,)
            ).fetchone()
        return _summary_from_row(row)

    async def get_summary(self, video_pk: int) -> Summary | None:
        """Fetch the stored notes for a video, or ``None`` when not generated."""
        return await asyncio.to_thread(self._get_summary, video_pk)

    def _get_summary(self, video_pk: int) -> Summary | None:
        with self._read() as conn:
            row = conn.execute("SELECT * FROM summaries WHERE video_id = ?", (video_pk,)).fetchone()
        return _summary_from_row(row) if row is not None else None

    async def edited_video_ids(self, video_pks: Iterable[int]) -> set[int]:
        """Return which of ``video_pks`` have hand-edited notes (for the list "Edited" tag)."""
        return await asyncio.to_thread(self._edited_video_ids, list(video_pks))

    def _edited_video_ids(self, video_pks: list[int]) -> set[int]:
        if not video_pks:
            return set()
        marks = ",".join("?" for _ in video_pks)
        with self._read() as conn:
            rows = conn.execute(
                f"SELECT video_id FROM summaries WHERE edited = 1 AND video_id IN ({marks})",  # noqa: S608
                video_pks,
            ).fetchall()
        return {int(row["video_id"]) for row in rows}

    async def mark_edited(self, video_pk: int) -> bool:
        """Flag inline-edited notes (design.md review screen)."""
        return await asyncio.to_thread(self._mark_edited, video_pk)

    def _mark_edited(self, video_pk: int) -> bool:
        with self._tx() as conn:
            cursor = conn.execute(
                "UPDATE summaries SET edited = 1 WHERE video_id = ?", (video_pk,)
            )
            return cursor.rowcount > 0

    async def approve(self, video_pk: int, *, approved_at: str | None = None) -> bool:
        """Approve notes and move the video to ``approved``; nothing goes to Drive here."""
        return await asyncio.to_thread(self._approve, video_pk, approved_at)

    def _approve(self, video_pk: int, approved_at: str | None) -> bool:
        stamp = approved_at or now_iso()
        with self._tx() as conn:
            cursor = conn.execute(
                "UPDATE summaries SET approved = 1, approved_at = ? WHERE video_id = ?",
                (stamp, video_pk),
            )
            if cursor.rowcount:
                conn.execute(
                    "UPDATE videos SET stage = ?, failure_type = NULL, failure_kind = NULL,"
                    " failure_reason = NULL, next_retry_at = NULL, updated_at = ? WHERE id = ?",
                    (Stage.APPROVED.value, now_iso(), video_pk),
                )
            return cursor.rowcount > 0

    async def approve_all_pending(
        self, playlist_pk: int, *, approved_at: str | None = None
    ) -> int:
        """Approve every ``pending_review`` video; returns how many were approved.

        Approved, published and failed videos are left exactly as they are — a
        bulk approval must never re-approve work that already shipped.
        """
        return await asyncio.to_thread(self._approve_all_pending, playlist_pk, approved_at)

    def _approve_all_pending(self, playlist_pk: int, approved_at: str | None) -> int:
        stamp = approved_at or now_iso()
        pending = (
            "(SELECT id FROM videos WHERE playlist_id = ? AND stage = ?)"
        )
        params = (stamp, playlist_pk, Stage.PENDING_REVIEW.value)
        with self._tx() as conn:
            cursor = conn.execute(
                f"UPDATE summaries SET approved = 1, approved_at = ? WHERE video_id IN {pending}",
                params,
            )
            changed = cursor.rowcount
            if changed:
                conn.execute(
                    f"UPDATE videos SET stage = ?, failure_type = NULL, failure_kind = NULL,"
                    f" failure_reason = NULL, next_retry_at = NULL, updated_at = ?"
                    f" WHERE id IN {pending}",
                    (Stage.APPROVED.value, now_iso(), playlist_pk, Stage.PENDING_REVIEW.value),
                )
        return changed

    async def requeue_all_failed(self, playlist_pk: int | None = None) -> int:
        """Re-queue every failed video (of one playlist, or all); returns how many.

        Same effect as a manual retry per video (failure columns cleared, retry budget
        reset, ``run_token`` bumped), in one transaction. Videos removed upstream are
        skipped, as the single-video retry refuses them.
        """
        return await asyncio.to_thread(self._requeue_all_failed, playlist_pk)

    def _requeue_all_failed(self, playlist_pk: int | None) -> int:
        scope = " AND playlist_id = ?" if playlist_pk is not None else ""
        params: list[object] = [Stage.QUEUED.value, now_iso(), Stage.FAILED.value]
        if playlist_pk is not None:
            params.append(playlist_pk)
        with self._tx() as conn:
            cursor = conn.execute(
                "UPDATE videos SET stage = ?, failure_type = NULL, failure_kind = NULL,"
                " failure_reason = NULL, retry_count = 0, next_retry_at = NULL,"
                " run_token = run_token + 1, updated_at = ?"
                " WHERE stage = ? AND removed_upstream = 0" + scope,
                params,
            )
            return cursor.rowcount

    async def mark_published(
        self, video_pk: int, digest: str | None = None, *, published_at: str | None = None
    ) -> bool:
        """Record a successful publish and move the video to ``published``."""
        return await asyncio.to_thread(self._mark_published, video_pk, digest, published_at)

    def _mark_published(
        self, video_pk: int, digest: str | None, published_at: str | None
    ) -> bool:
        stamp = published_at or now_iso()
        with self._tx() as conn:
            # Publishing runs in the background: a regenerate that lands while the
            # Doc is being written moves the video back to an earlier stage, and its
            # new, unreviewed notes must not be stamped as published.
            cursor = conn.execute(
                "UPDATE summaries SET published = 1, published_at = ?, published_digest = ?"
                " WHERE video_id = ?"
                " AND EXISTS (SELECT 1 FROM videos WHERE id = ? AND stage IN (?, ?))",
                (stamp, digest, video_pk, video_pk,
                 Stage.APPROVED.value, Stage.PUBLISHED.value),
            )
            if cursor.rowcount:
                conn.execute(
                    "UPDATE videos SET stage = ?, updated_at = ? WHERE id = ?",
                    (Stage.PUBLISHED.value, now_iso(), video_pk),
                )
            return cursor.rowcount > 0

    async def regenerate(
        self,
        video_pk: int,
        content: str,
        provider: str | None = None,
        model: str | None = None,
        prompt_version: str | None = None,
        *,
        generated_at: str | None = None,
        token: int | None = None,
    ) -> Summary:
        """Overwrite notes and return the video to ``pending_review``.

        Approval and publish state is cleared (including ``published_digest``) so
        the video cannot be published again until a human re-approves it. The
        video's ``drive_doc_id`` is kept, so the re-publish updates the Doc in place.
        """
        return await asyncio.to_thread(
            self._regenerate,
            video_pk,
            content,
            provider,
            model,
            prompt_version,
            generated_at,
            token,
        )

    def _regenerate(
        self,
        video_pk: int,
        content: str,
        provider: str | None,
        model: str | None,
        prompt_version: str | None,
        generated_at: str | None,
        token: int | None,
    ) -> Summary:
        stamp = generated_at or now_iso()
        with self._tx() as conn:
            self._check_token(conn, video_pk, token)
            conn.execute(
                "INSERT INTO summaries (video_id, content, provider, model, prompt_version,"
                " generated_at) VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (video_id) DO UPDATE SET content = excluded.content,"
                " provider = excluded.provider, model = excluded.model,"
                " prompt_version = excluded.prompt_version, generated_at = excluded.generated_at,"
                " edited = 0, approved = 0, approved_at = NULL, published = 0,"
                " published_at = NULL, published_digest = NULL",
                (video_pk, content, provider, model, prompt_version, stamp),
            )
            conn.execute(
                "UPDATE videos SET stage = ?, failure_type = NULL, failure_kind = NULL,"
                " failure_reason = NULL, next_retry_at = NULL, regen_requested = 0,"
                " regen_prompt = NULL, updated_at = ? WHERE id = ?",
                (Stage.PENDING_REVIEW.value, now_iso(), video_pk),
            )
            row = conn.execute("SELECT * FROM summaries WHERE video_id = ?", (video_pk,)).fetchone()
        return _summary_from_row(row)

    async def count_published(self, playlist_pk: int) -> int:
        """How many videos of the playlist have reached ``published``."""
        return await asyncio.to_thread(self._count_published, playlist_pk)

    def _count_published(self, playlist_pk: int) -> int:
        with self._read() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM videos WHERE playlist_id = ? AND stage = ?",
                (playlist_pk, Stage.PUBLISHED.value),
            ).fetchone()
        return int(row["total"]) if row is not None else 0
