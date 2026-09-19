"""Connection management and numbered SQL migration runner for the Atlas store."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from backend.storage.migrations import MIGRATIONS


def now_iso() -> str:
    """Current UTC time as an ISO-8601 string (second precision)."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def now_epoch() -> float:
    """Current wall-clock time as a Unix epoch float."""
    from time import time

    return time()


def epoch_of(iso_value: str | None) -> float:
    """Convert an ISO-8601 timestamp to a Unix epoch float (0 when unparseable)."""
    if not iso_value:
        return 0.0
    try:
        return datetime.fromisoformat(iso_value).timestamp()
    except (TypeError, ValueError):
        return 0.0


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a connection with WAL, foreign keys, and a busy timeout.

    Uses autocommit mode (``isolation_level=None``); explicit transactions are
    started with :func:`transaction` so write boundaries stay short and explicit.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def initialize(db_path: str | Path) -> None:
    """Create the database (if needed) and apply any pending migrations."""
    conn = connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version TEXT PRIMARY KEY,"
            " applied_at TEXT NOT NULL)"
        )
        applied = {
            row["version"] for row in conn.execute("SELECT version FROM schema_migrations")
        }
        for version, statements in MIGRATIONS:
            if version in applied:
                continue
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in statements:
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, now_iso()),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a short write transaction with explicit commit/rollback."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
