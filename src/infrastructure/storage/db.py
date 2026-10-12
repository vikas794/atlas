"""Connection management and numbered SQL migration runner for the Atlas store."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from src.infrastructure.storage.migrations import FOREIGN_KEYS_OFF, MIGRATIONS

logger = logging.getLogger(__name__)


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


def _backup_before_migrating(conn: sqlite3.Connection, db_path: Path, version: str) -> None:
    """Copy an existing, populated database aside before its first pending migration.

    Migration 0003 drops the legacy search-run tables and 0004 rebuilds ``videos``;
    a plain copy next to the database is the cheapest safety net. Best effort: a
    failed backup is logged and never blocks startup.
    """
    target = db_path.with_name(f"{db_path.name}.pre-{version}.bak")
    if target.exists():
        return
    try:
        dest = sqlite3.connect(str(target))
        try:
            conn.backup(dest)
        finally:
            dest.close()
        logger.info("Backed up %s to %s before applying migration %s", db_path, target, version)
    except Exception:
        logger.warning("Could not back up %s before migration %s", db_path, version, exc_info=True)


def initialize(db_path: str | Path) -> None:
    """Create the database (if needed) and apply any pending migrations.

    When an existing database that already has applied migrations is about to
    receive a new one, a ``<db>.pre-<version>.bak`` copy is written first.
    """
    db_path = Path(db_path)
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
        pending = [m for m in MIGRATIONS if m[0] not in applied]
        if applied and pending:
            _backup_before_migrating(conn, db_path, pending[0][0])
        for version, statements in pending:
            fk_off = version in FOREIGN_KEYS_OFF
            if fk_off:
                # A no-op inside a transaction, so it must be set before BEGIN.
                conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in statements:
                    conn.execute(statement)
                if fk_off:
                    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
                    if violations:
                        raise sqlite3.IntegrityError(
                            f"Migration {version} left {len(violations)} foreign key violation(s)"
                        )
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, now_iso()),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                if fk_off:
                    conn.execute("PRAGMA foreign_keys=ON")
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
