"""Schema migrations must never silently destroy data (PRD section 21).

Migration 0003 retires the legacy search-run tables (documented, and now preceded by an
automatic ``.bak`` copy); migration 0004 rebuilds ``videos`` and must keep every transcript,
summary and Drive id while changing the uniqueness rule and shifting positions to 1-based.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.infrastructure.storage import db
from src.infrastructure.storage.migrations import MIGRATIONS


def apply_up_to(db_path: Path, last_version: str) -> None:
    """Create a database exactly as an older Atlas release would have left it."""
    conn = db.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY,"
            " applied_at TEXT NOT NULL)"
        )
        for version, statements in MIGRATIONS:
            for statement in statements:
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, db.now_iso()),
            )
            if version == last_version:
                break
    finally:
        conn.close()


def seed_0003(db_path: Path) -> None:
    """A playlist stored by the pre-0004 code: 0-based positions, transcripts and summaries."""
    conn = db.connect(db_path)
    try:
        stamp = db.now_iso()
        conn.execute(
            "INSERT INTO playlists (id, playlist_id, source_url, title, video_count, status,"
            " drive_folder_id, drive_index_doc_id, created_at, updated_at)"
            " VALUES (1, 'PL1', 'u', 'Course', 3, 'ingested', 'folder-1', 'index-1', ?, ?)",
            (stamp, stamp),
        )
        for pk, (vid, pos, doc) in enumerate(
            [("a", 0, "doc-a"), ("b", 1, None), ("c", 2, "doc-c")], start=10
        ):
            conn.execute(
                "INSERT INTO videos (id, playlist_id, youtube_video_id, title, position, stage,"
                " drive_doc_id, created_at, updated_at) VALUES (?, 1, ?, ?, ?, 'published', ?, ?, ?)",
                (pk, vid, f"Title {vid}", pos, doc, stamp, stamp),
            )
            conn.execute(
                "INSERT INTO transcripts (video_id, content, fetched_at) VALUES (?, ?, ?)",
                (pk, f"transcript {vid}", stamp),
            )
            conn.execute(
                "INSERT INTO summaries (video_id, content, generated_at) VALUES (?, ?, ?)",
                (pk, f"notes {vid}", stamp),
            )
    finally:
        conn.close()


def test_0004_keeps_every_row_transcript_summary_and_drive_id(tmp_path: Path) -> None:
    db_path = tmp_path / "old.sqlite3"
    apply_up_to(db_path, "0003_playlist_pipeline")
    seed_0003(db_path)

    db.initialize(db_path)  # applies 0004

    conn = db.connect(db_path)
    try:
        videos = conn.execute("SELECT id, youtube_video_id, position, drive_doc_id, stage"
                              " FROM videos ORDER BY id").fetchall()
        assert [(v["id"], v["youtube_video_id"]) for v in videos] == [(10, "a"), (11, "b"), (12, "c")]
        # 0-based -> 1-based; Drive ids and stages are untouched.
        assert [v["position"] for v in videos] == [1, 2, 3]
        assert [v["drive_doc_id"] for v in videos] == ["doc-a", None, "doc-c"]
        assert {v["stage"] for v in videos} == {"published"}
        assert conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM summaries").fetchone()[0] == 3
        assert conn.execute("SELECT content FROM summaries WHERE video_id = 11").fetchone()[0] == "notes b"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1  # re-enabled afterwards
        playlist = conn.execute("SELECT * FROM playlists").fetchone()
        assert (playlist["drive_folder_id"], playlist["pad_width"], playlist["index_digest"]) == (
            "folder-1", 0, None)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(videos)")}
        assert {"duplicate_of_video_id", "run_token", "regen_requested"} <= columns
    finally:
        conn.close()


def test_0004_foreign_keys_still_cascade_after_the_table_rebuild(tmp_path: Path) -> None:
    db_path = tmp_path / "old.sqlite3"
    apply_up_to(db_path, "0003_playlist_pipeline")
    seed_0003(db_path)
    db.initialize(db_path)

    conn = db.connect(db_path)
    try:
        conn.execute("DELETE FROM playlists WHERE id = 1")
        assert conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM summaries").fetchone()[0] == 0
    finally:
        conn.close()


def test_0004_replaces_the_video_uniqueness_rule_with_one_row_per_position(tmp_path: Path) -> None:
    db_path = tmp_path / "old.sqlite3"
    apply_up_to(db_path, "0003_playlist_pipeline")
    seed_0003(db_path)
    db.initialize(db_path)

    conn = db.connect(db_path)
    try:
        stamp = db.now_iso()
        insert = (
            "INSERT INTO videos (playlist_id, youtube_video_id, position, created_at, updated_at)"
            " VALUES (1, ?, ?, ?, ?)"
        )
        conn.execute(insert, ("a", 4, stamp, stamp))  # the same video again at a new position
        try:
            conn.execute(insert, ("zzz", 4, stamp, stamp))  # same position: rejected
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("duplicate position was accepted")
    finally:
        conn.close()


def test_a_playlist_already_numbered_from_one_is_not_shifted(tmp_path: Path) -> None:
    db_path = tmp_path / "old.sqlite3"
    apply_up_to(db_path, "0003_playlist_pipeline")
    conn = db.connect(db_path)
    stamp = db.now_iso()
    conn.execute("INSERT INTO playlists (id, playlist_id, source_url, created_at, updated_at)"
                 " VALUES (1, 'PL', 'u', ?, ?)", (stamp, stamp))
    for position, vid in [(1, "a"), (2, "b")]:
        conn.execute("INSERT INTO videos (playlist_id, youtube_video_id, position, created_at,"
                     " updated_at) VALUES (1, ?, ?, ?, ?)", (vid, position, stamp, stamp))
    conn.close()

    db.initialize(db_path)

    conn = db.connect(db_path)
    try:
        assert [r[0] for r in conn.execute("SELECT position FROM videos ORDER BY id")] == [1, 2]
    finally:
        conn.close()


def test_a_populated_database_is_backed_up_before_its_first_pending_migration(tmp_path: Path) -> None:
    db_path = tmp_path / "old.sqlite3"
    apply_up_to(db_path, "0003_playlist_pipeline")
    seed_0003(db_path)

    db.initialize(db_path)

    backup = tmp_path / "old.sqlite3.pre-0004_duplicates_publish_state.bak"
    assert backup.exists()
    copy = sqlite3.connect(backup)
    try:
        # The backup is the *old* schema with every row intact.
        assert copy.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0] == 3
        columns = {row[1] for row in copy.execute("PRAGMA table_info(videos)")}
        assert "run_token" not in columns
    finally:
        copy.close()


def test_migration_0003_is_preceded_by_a_backup_holding_the_legacy_tables(tmp_path: Path) -> None:
    """0003 drops the legacy search-run tables; the .bak is the way back."""
    db_path = tmp_path / "legacy.sqlite3"
    apply_up_to(db_path, "0002_usage_ledger_and_extended_cache")
    conn = db.connect(db_path)
    stamp = db.now_iso()
    conn.execute(
        "INSERT INTO runs (run_id, search_query, created_at, updated_at, source_folder)"
        " VALUES ('r1', 'java', ?, ?, 'x')", (stamp, stamp))
    conn.close()

    db.initialize(db_path)

    backup = tmp_path / "legacy.sqlite3.pre-0003_playlist_pipeline.bak"
    assert backup.exists()
    copy = sqlite3.connect(backup)
    try:
        assert copy.execute("SELECT search_query FROM runs").fetchone()[0] == "java"
    finally:
        copy.close()
    live = db.connect(db_path)
    try:
        tables = {r[0] for r in live.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "runs" not in tables and "playlists" in tables
    finally:
        live.close()


def test_a_fresh_database_needs_no_backup_and_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.sqlite3"

    db.initialize(db_path)
    db.initialize(db_path)  # second run applies nothing

    assert list(tmp_path.glob("*.bak")) == []
    conn = db.connect(db_path)
    try:
        applied = [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
        assert applied == [m[0] for m in MIGRATIONS]
    finally:
        conn.close()
