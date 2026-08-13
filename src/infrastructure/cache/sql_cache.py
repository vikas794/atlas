from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import zlib
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from backend.storage.database import connect, now_iso, transaction

if TYPE_CHECKING:
    from src.domain.interfaces.cache import CacheKey
    from src.domain.interfaces.usage_ledger import UsageLedgerPort

logger = logging.getLogger(__name__)


class SqlCacheAdapter:
    """SQLite-backed cache adapter implementing CachePort.

    Uses the `cache_entries` table extended with namespace, version,
    content_hash, params_hash, and value columns for structured cache keys.
    """

    def __init__(
        self,
        db_path: str | Path,
        usage_ledger: UsageLedgerPort | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.usage_ledger = usage_ledger
        self.run_id = "global-cache"
        self._ensure_run_id()

    def _conn(self) -> sqlite3.Connection:
        return connect(self.db_path)

    @contextmanager
    def _tx(self):
        conn = self._conn()
        try:
            with transaction(conn) as tx:
                yield tx
        finally:
            conn.close()

    def now_iso(self) -> str:
        return now_iso()

    def _ensure_run_id(self) -> None:
        """Ensure the referenced run_id exists in the runs table."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT run_id FROM runs WHERE run_id = ?",
                (self.run_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO runs (run_id, search_query, normalized_query, status,
                    source_folder, created_at, updated_at, is_fallback)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.run_id,
                        "",
                        "",
                        "succeeded",
                        "",
                        self.now_iso(),
                        self.now_iso(),
                        0,
                    ),
                )

    async def get(self, key: CacheKey) -> bytes | None:
        """Retrieve a cached value by composite key.

        Looks up by (namespace, version, content_hash, params_hash).
        Returns None if not found, expired, or on error.
        """
        try:
            with self._conn() as conn:
                row = conn.execute(
                    """
                    SELECT value, expires_at
                    FROM cache_entries
                    WHERE namespace = ? AND version = ? AND content_hash = ? AND params_hash = ? AND run_id = ?
                    """,
                    (key.namespace, key.version, key.content_hash, key.params_hash, self.run_id),
                ).fetchone()

            if row is None:
                if self.usage_ledger:
                    await self._record_miss(key)
                return None

            expires_at = row["expires_at"]
            if expires_at and expires_at <= self.now_iso():
                if self.usage_ledger:
                    await self._record_miss(key)
                return None

            value_blob = row["value"]
            if value_blob is None:
                if self.usage_ledger:
                    await self._record_miss(key)
                return None

            try:
                decompressed = zlib.decompress(value_blob).decode("utf-8")
                value = json.loads(decompressed).encode("utf-8")
            except (zlib.error, json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.warning("Cache value corruption for key %s: %s", key, e)
                if self.usage_ledger:
                    await self._record_miss(key)
                return None

            await self._record_hit(key)

            return value

        except sqlite3.Error as e:
            logger.warning("Cache get error for key %s: %s", key, e)
            if self.usage_ledger:
                await self._record_miss(key)
            return None

    async def set(self, key: CacheKey, value: bytes, ttl: timedelta) -> None:
        """Store a value in cache with TTL.

        Compresses value with zlib before storing.
        Uses INSERT OR REPLACE for upsert semantics.
        """
        expires_at = (datetime.now(UTC) + ttl).isoformat(timespec="seconds")
        try:
            # Generate cache_key from the four components for backward compatibility
            cache_key_str = f"{key.namespace}|{key.version}|{key.content_hash}|{key.params_hash}"
            cache_key_hash = hashlib.sha256(cache_key_str.encode("utf-8")).hexdigest()

            compressed = zlib.compress(json.dumps(value.decode("utf-8")).encode("utf-8"))
        except (UnicodeDecodeError, TypeError) as e:
            logger.warning("Cache set encoding error for key %s: %s", key, e)
            return

        try:
            with self._tx() as conn:
                # Debug: print the parameters
                params = (
                    cache_key_hash,           # cache_key: hash of the four components
                    key.namespace,            # kind: use namespace as kind
                    self.run_id,              # run_id: general caching run
                    "",                       # normalized_query: default empty string
                    "",                       # settings_hash: default NULL
                    "succeeded",              # status: successful cache entry
                    self.now_iso(),           # created_at: now
                    expires_at,               # expires_at: now + ttl
                    key.namespace,            # namespace: from key
                    key.version,              # version: from key
                    key.content_hash,         # content_hash: from key
                    key.params_hash,          # params_hash: from key
                    compressed,               # value: compressed cached data
                )
                print(f"DEBUG: About to execute INSERT with {len(params)} params")
                for i, p in enumerate(params):
                    print(f"  Param {i+1}: {repr(p)}")

                sql = """
                    INSERT OR REPLACE INTO cache_entries
                    (cache_key, kind, run_id, normalized_query, settings_hash, status,
                     created_at, expires_at, namespace, version, content_hash, params_hash, value)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """
                print(f"DEBUG: SQL length: {len(sql)} chars")
                print(f"DEBUG: SQL count of '?': {sql.count('?')}")
                print(f"DEBUG: SQL: {repr(sql)}")

                conn.execute(sql, params)
                print("DEBUG: INSERT executed successfully")
        except sqlite3.Error as e:
            logger.warning("Cache set error for key %s: %s", key, e)

    async def invalidate(self, key: CacheKey) -> None:
        """Remove a specific cache entry by composite key."""
        try:
            with self._tx() as conn:
                conn.execute(
                    """
                    DELETE FROM cache_entries
                    WHERE namespace = ? AND version = ? AND content_hash = ? AND params_hash = ? AND run_id = ?
                    """,
                    (key.namespace, key.version, key.content_hash, key.params_hash, self.run_id),
                )
        except sqlite3.Error as e:
            logger.warning("Cache invalidate error for key %s: %s", key, e)

    async def invalidate_namespace(self, namespace: str) -> None:
        """Remove all cache entries for a given namespace."""
        try:
            with self._tx() as conn:
                conn.execute(
                    "DELETE FROM cache_entries WHERE namespace = ?",
                    (namespace,),
                )
        except sqlite3.Error as e:
            logger.warning("Cache invalidate_namespace error for namespace %s: %s", namespace, e)

    async def _record_hit(self, key: CacheKey) -> None:
        """Record a cache hit and update hit tracking."""
        try:
            with self._tx() as conn:
                conn.execute(
                    """
                    UPDATE cache_entries
                    SET hit_count = hit_count + 1, last_hit_at = ?
                    WHERE namespace = ? AND version = ? AND content_hash = ? AND params_hash = ? AND run_id = ?
                    """,
                    (self.now_iso(), key.namespace, key.version, key.content_hash, key.params_hash, self.run_id),
                )
        except sqlite3.Error as e:
            logger.warning("Cache hit recording error for key %s: %s", key, e)

        if self.usage_ledger:
            try:
                from src.domain.interfaces.usage_ledger import UsageRecord
                record = UsageRecord(
                    timestamp=datetime.now(UTC),
                    provider="cache",
                    operation="get",
                    model="",
                    input_tokens=0,
                    output_tokens=0,
                    total_tokens=0,
                    cost_usd=0.0,
                    cache_hit=True,
                    run_id=None,
                    video_id=None,
                )
                await self.usage_ledger.record_usage(record)
            except Exception as e:
                logger.warning("Usage ledger record error on cache hit: %s", e)

    async def _record_miss(self, key: CacheKey) -> None:
        """Record a cache miss via usage ledger."""
        if self.usage_ledger:
            try:
                from src.domain.interfaces.usage_ledger import UsageRecord
                record = UsageRecord(
                    timestamp=datetime.now(UTC),
                    provider="cache",
                    operation="get",
                    model="",
                    input_tokens=0,
                    output_tokens=0,
                    total_tokens=0,
                    cost_usd=0.0,
                    cache_hit=False,
                    run_id=None,
                    video_id=None,
                )
                await self.usage_ledger.record_usage(record)
            except Exception as e:
                logger.warning("Usage ledger record error on cache miss: %s", e)

    async def delete(self, key: CacheKey) -> None:
        """Protocol method: alias for invalidate."""
        await self.invalidate(key)

    async def exists(self, key: CacheKey) -> bool:
        """Protocol method: check if key exists and is not expired."""
        result = await self.get(key)
        return result is not None

    async def touch(self, key: CacheKey) -> None:
        """Protocol method: update last access time (hit tracking)."""
        await self._record_hit(key)
