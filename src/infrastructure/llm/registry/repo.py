"""SQLite storage for the model registry (tables from migration 0007).

Plain CRUD plus the cooldown and usage ledgers. All policy (which key is usable, how long a
cooldown lasts) lives in :mod:`src.infrastructure.llm.registry.service`.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.infrastructure.storage.db import connect, initialize, now_iso, transaction

#: Cooldown scope meaning "every model on this key".
ALL_MODELS = "*"

_MODEL_FIELDS = (
    "display_name",
    "api_base",
    "input_token_limit",
    "request_timeout_seconds",
    "rpm_limit",
    "rpd_limit",
    "tpm_limit",
    "tpd_limit",
    "enabled",
)
_KEY_FIELDS = (
    "label",
    "key_value",
    "enabled",
    "monthly_request_cap",
    "monthly_token_cap",
)


@dataclass(frozen=True, slots=True)
class ModelRow:
    id: int
    provider: str
    model_id: str
    display_name: str
    litellm_model: str
    api_base: str | None
    input_token_limit: int
    request_timeout_seconds: float | None
    rpm_limit: int | None
    rpd_limit: int | None
    tpm_limit: int | None
    tpd_limit: int | None
    enabled: bool
    source: str
    priority: int | None = None
    chain_enabled: bool = False
    hidden: bool = False


@dataclass(frozen=True, slots=True)
class KeyRow:
    id: int
    provider: str
    label: str
    key_value: str
    status: str
    enabled: bool
    consecutive_failures: int
    last_checked_at: str | None
    last_healthy_at: str | None
    last_health_error: str | None
    monthly_request_cap: int | None
    monthly_token_cap: int | None
    created_at: str


def _model(row: sqlite3.Row) -> ModelRow:
    keys = row.keys()
    return ModelRow(
        id=row["id"],
        provider=row["provider"],
        model_id=row["model_id"],
        display_name=row["display_name"],
        litellm_model=row["litellm_model"],
        api_base=row["api_base"],
        input_token_limit=row["input_token_limit"],
        request_timeout_seconds=row["request_timeout_seconds"],
        rpm_limit=row["rpm_limit"],
        rpd_limit=row["rpd_limit"],
        tpm_limit=row["tpm_limit"],
        tpd_limit=row["tpd_limit"],
        enabled=bool(row["enabled"]),
        source=row["source"],
        priority=row["priority"] if "priority" in keys else None,
        chain_enabled=bool(row["chain_enabled"]) if "chain_enabled" in keys else False,
        hidden=bool(row["hidden"]) if "hidden" in keys else False,
    )


def _key(row: sqlite3.Row) -> KeyRow:
    return KeyRow(
        id=row["id"],
        provider=row["provider"],
        label=row["label"],
        key_value=row["key_value"],
        status=row["status"],
        enabled=bool(row["enabled"]),
        consecutive_failures=row["consecutive_failures"],
        last_checked_at=row["last_checked_at"],
        last_healthy_at=row["last_healthy_at"],
        last_health_error=row["last_health_error"],
        monthly_request_cap=row["monthly_request_cap"],
        monthly_token_cap=row["monthly_token_cap"],
        created_at=row["created_at"],
    )


_MODEL_SELECT = """
    SELECT m.*, f.priority AS priority, COALESCE(f.enabled, 0) AS chain_enabled
    FROM llm_models m LEFT JOIN llm_fallback f ON f.model_id = m.id
"""


def _renumber_chain(conn: sqlite3.Connection) -> None:
    """Dense priorities 1..N, hidden models last, everything else keeping its order."""
    rows = conn.execute(
        "SELECT f.model_id FROM llm_fallback f JOIN llm_models m ON m.id = f.model_id "
        "ORDER BY m.hidden, f.priority, f.model_id"
    ).fetchall()
    for priority, row in enumerate(rows, start=1):
        conn.execute(
            "UPDATE llm_fallback SET priority = ? WHERE model_id = ?", (priority, row["model_id"])
        )


class RegistryRepo:
    """One short-lived connection per call, like the other repositories."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        initialize(self._db_path)

    def _conn(self) -> sqlite3.Connection:
        return connect(self._db_path)

    # --- meta ------------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        conn = self._conn()
        try:
            row = conn.execute("SELECT value FROM llm_meta WHERE key = ?", (key,)).fetchone()
        finally:
            conn.close()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO llm_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        finally:
            conn.close()

    def saved_settings(self) -> dict[str, Any]:
        """The UI-saved ``app_settings`` rows (key -> decoded value); used by the first-run import."""
        conn = self._conn()
        try:
            rows = conn.execute("SELECT key, value_json FROM app_settings").fetchall()
        except sqlite3.Error:
            return {}
        finally:
            conn.close()
        saved: dict[str, Any] = {}
        for row in rows:
            try:
                saved[row["key"]] = json.loads(row["value_json"])
            except json.JSONDecodeError:
                continue
        return saved

    # --- models ----------------------------------------------------------

    def list_models(self) -> list[ModelRow]:
        """Every model; chain members first in priority order, the rest after."""
        conn = self._conn()
        try:
            rows = conn.execute(
                _MODEL_SELECT
                + " ORDER BY (f.priority IS NULL), f.priority, m.id"
            ).fetchall()
        finally:
            conn.close()
        return [_model(r) for r in rows]

    def get_model(self, model_pk: int) -> ModelRow | None:
        conn = self._conn()
        try:
            row = conn.execute(_MODEL_SELECT + " WHERE m.id = ?", (model_pk,)).fetchone()
        finally:
            conn.close()
        return _model(row) if row else None

    def add_model(
        self,
        *,
        provider: str,
        model_id: str,
        display_name: str,
        litellm_model: str,
        api_base: str | None,
        input_token_limit: int,
        request_timeout_seconds: float | None = None,
        rpm_limit: int | None = None,
        rpd_limit: int | None = None,
        tpm_limit: int | None = None,
        tpd_limit: int | None = None,
        enabled: bool = True,
        source: str = "user",
    ) -> ModelRow:
        """Insert a model at the end of the chain. Raises ``sqlite3.IntegrityError`` on a duplicate."""
        conn = self._conn()
        try:
            with transaction(conn):
                cur = conn.execute(
                    """
                    INSERT INTO llm_models (
                        provider, model_id, display_name, litellm_model, api_base,
                        input_token_limit, request_timeout_seconds, rpm_limit, rpd_limit,
                        tpm_limit, tpd_limit, enabled, source, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        provider, model_id, display_name, litellm_model, api_base,
                        input_token_limit, request_timeout_seconds, rpm_limit, rpd_limit,
                        tpm_limit, tpd_limit, int(enabled), source, now_iso(),
                    ),
                )
                pk = int(cur.lastrowid or 0)
                top = conn.execute("SELECT COALESCE(MAX(priority), 0) FROM llm_fallback").fetchone()
                conn.execute(
                    "INSERT INTO llm_fallback (model_id, priority, enabled) VALUES (?, ?, ?)",
                    (pk, int(top[0]) + 1, int(enabled)),
                )
        finally:
            conn.close()
        model = self.get_model(pk)
        assert model is not None
        return model

    def update_model(self, model_pk: int, changes: dict[str, Any]) -> ModelRow | None:
        fields = {k: v for k, v in changes.items() if k in _MODEL_FIELDS}
        conn = self._conn()
        try:
            with transaction(conn):
                flag = conn.execute(
                    "SELECT hidden FROM llm_models WHERE id = ?", (model_pk,)
                ).fetchone()
                if flag and flag["hidden"]:
                    fields.pop("enabled", None)  # a hidden model cannot be switched on
                if fields:
                    values = [int(v) if k == "enabled" else v for k, v in fields.items()]
                    conn.execute(
                        f"UPDATE llm_models SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                        (*values, model_pk),
                    )
                if "enabled" in fields:
                    conn.execute(
                        "UPDATE llm_fallback SET enabled = ? WHERE model_id = ?",
                        (int(fields["enabled"]), model_pk),
                    )
        finally:
            conn.close()
        return self.get_model(model_pk)

    def delete_model(self, model_pk: int) -> bool:
        conn = self._conn()
        try:
            with transaction(conn):
                cur = conn.execute("DELETE FROM llm_models WHERE id = ?", (model_pk,))
                if cur.rowcount > 0:
                    _renumber_chain(conn)
        finally:
            conn.close()
        return cur.rowcount > 0

    def delete_models(self, model_pks: list[int]) -> int:
        """Delete every listed model (the chain rows cascade) and re-rank the rest; unknown ids are ignored."""
        conn = self._conn()
        try:
            with transaction(conn):
                count = 0
                for pk in dict.fromkeys(model_pks):
                    count += conn.execute("DELETE FROM llm_models WHERE id = ?", (pk,)).rowcount
                if count:
                    _renumber_chain(conn)
        finally:
            conn.close()
        return count

    def set_hidden(self, model_pks: list[int], hidden: bool) -> int:
        """Hide or unhide models; hiding also switches them off. Unknown ids are ignored."""
        conn = self._conn()
        try:
            with transaction(conn):
                count = 0
                for pk in dict.fromkeys(model_pks):
                    if hidden:
                        cur = conn.execute(
                            "UPDATE llm_models SET hidden = 1, enabled = 0 WHERE id = ?", (pk,)
                        )
                        conn.execute("UPDATE llm_fallback SET enabled = 0 WHERE model_id = ?", (pk,))
                    else:
                        cur = conn.execute("UPDATE llm_models SET hidden = 0 WHERE id = ?", (pk,))
                    count += cur.rowcount
                _renumber_chain(conn)
        finally:
            conn.close()
        return count

    # --- fallback chain --------------------------------------------------

    def replace_chain(self, entries: list[tuple[int, int, bool]]) -> None:
        """Set ``(model_pk, priority, enabled)`` for every listed model; unknown ids are skipped.

        A hidden model keeps its priority but is never switched on.
        """
        conn = self._conn()
        try:
            with transaction(conn):
                known = {r["id"] for r in conn.execute("SELECT id FROM llm_models")}
                hidden = {r["id"] for r in conn.execute("SELECT id FROM llm_models WHERE hidden = 1")}
                for model_pk, priority, enabled in entries:
                    if model_pk not in known:
                        continue
                    if model_pk in hidden:
                        enabled = False
                    conn.execute(
                        "INSERT INTO llm_fallback (model_id, priority, enabled) VALUES (?, ?, ?) "
                        "ON CONFLICT(model_id) DO UPDATE SET "
                        "priority = excluded.priority, enabled = excluded.enabled",
                        (model_pk, priority, int(enabled)),
                    )
                    conn.execute(
                        "UPDATE llm_models SET enabled = ? WHERE id = ?", (int(enabled), model_pk)
                    )
        finally:
            conn.close()

    # --- keys ------------------------------------------------------------

    def list_keys(self, provider: str | None = None) -> list[KeyRow]:
        conn = self._conn()
        try:
            if provider:
                rows = conn.execute(
                    "SELECT * FROM llm_api_keys WHERE provider = ? ORDER BY id", (provider,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM llm_api_keys ORDER BY provider, id").fetchall()
        finally:
            conn.close()
        return [_key(r) for r in rows]

    def get_key(self, key_pk: int) -> KeyRow | None:
        conn = self._conn()
        try:
            row = conn.execute("SELECT * FROM llm_api_keys WHERE id = ?", (key_pk,)).fetchone()
        finally:
            conn.close()
        return _key(row) if row else None

    def add_key(
        self,
        *,
        provider: str,
        label: str,
        key_value: str,
        monthly_request_cap: int | None = None,
        monthly_token_cap: int | None = None,
        enabled: bool = True,
    ) -> KeyRow:
        conn = self._conn()
        try:
            cur = conn.execute(
                """
                INSERT INTO llm_api_keys (
                    provider, label, key_value, enabled, monthly_request_cap,
                    monthly_token_cap, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provider, label, key_value, int(enabled), monthly_request_cap,
                    monthly_token_cap, now_iso(),
                ),
            )
            pk = int(cur.lastrowid or 0)
        finally:
            conn.close()
        key = self.get_key(pk)
        assert key is not None
        return key

    def update_key(self, key_pk: int, changes: dict[str, Any]) -> KeyRow | None:
        """Apply ``changes``. A new key value resets status and failures and clears cooldowns."""
        fields = {k: v for k, v in changes.items() if k in _KEY_FIELDS}
        conn = self._conn()
        try:
            with transaction(conn):
                if fields:
                    values = [int(v) if k == "enabled" else v for k, v in fields.items()]
                    conn.execute(
                        f"UPDATE llm_api_keys SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                        (*values, key_pk),
                    )
                if "key_value" in fields:
                    conn.execute(
                        "UPDATE llm_api_keys SET status = 'unknown', consecutive_failures = 0, "
                        "last_health_error = NULL WHERE id = ?",
                        (key_pk,),
                    )
                    conn.execute("DELETE FROM llm_cooldowns WHERE key_id = ?", (key_pk,))
                if fields.get("enabled"):
                    conn.execute(
                        "UPDATE llm_api_keys SET consecutive_failures = 0 WHERE id = ?", (key_pk,)
                    )
        finally:
            conn.close()
        return self.get_key(key_pk)

    def delete_key(self, key_pk: int) -> bool:
        conn = self._conn()
        try:
            cur = conn.execute("DELETE FROM llm_api_keys WHERE id = ?", (key_pk,))
        finally:
            conn.close()
        return cur.rowcount > 0

    def set_key_health(
        self,
        key_pk: int,
        *,
        status: str,
        error: str | None,
        consecutive_failures: int,
        enabled: bool | None = None,
    ) -> None:
        stamp = now_iso()
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE llm_api_keys SET status = ?, last_health_error = ?, "
                "consecutive_failures = ?, last_checked_at = ?, "
                "last_healthy_at = CASE WHEN ? = 'healthy' THEN ? ELSE last_healthy_at END, "
                "enabled = COALESCE(?, enabled) WHERE id = ?",
                (
                    status, error, consecutive_failures, stamp, status, stamp,
                    None if enabled is None else int(enabled), key_pk,
                ),
            )
        finally:
            conn.close()

    # --- cooldowns -------------------------------------------------------

    def get_cooldown(self, key_pk: int, model_scope: str) -> tuple[float, str, float] | None:
        """``(expires_at, source, set_at)`` for the scope, if any."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT expires_at, source, set_at FROM llm_cooldowns "
                "WHERE key_id = ? AND model_id = ?",
                (key_pk, model_scope),
            ).fetchone()
        finally:
            conn.close()
        return (row["expires_at"], row["source"], row["set_at"]) if row else None

    def active_cooldowns(self, now: float) -> list[sqlite3.Row]:
        conn = self._conn()
        try:
            return conn.execute(
                "SELECT key_id, model_id, expires_at, source FROM llm_cooldowns WHERE expires_at > ?",
                (now,),
            ).fetchall()
        finally:
            conn.close()

    def set_cooldown(
        self, key_pk: int, model_scope: str, expires_at: float, source: str, now: float
    ) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO llm_cooldowns (key_id, model_id, expires_at, source, set_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(key_id, model_id) DO UPDATE SET "
                "expires_at = excluded.expires_at, source = excluded.source, "
                "set_at = excluded.set_at",
                (key_pk, model_scope, expires_at, source, now),
            )
        finally:
            conn.close()

    def clear_cooldowns(self, key_pk: int, model_scope: str | None = None) -> int:
        conn = self._conn()
        try:
            if model_scope is None:
                cur = conn.execute("DELETE FROM llm_cooldowns WHERE key_id = ?", (key_pk,))
            else:
                cur = conn.execute(
                    "DELETE FROM llm_cooldowns WHERE key_id = ? AND model_id = ?",
                    (key_pk, model_scope),
                )
        finally:
            conn.close()
        return cur.rowcount

    def sweep_cooldowns(self, now: float) -> None:
        conn = self._conn()
        try:
            conn.execute("DELETE FROM llm_cooldowns WHERE expires_at <= ?", (now,))
        finally:
            conn.close()

    # --- usage ledgers ---------------------------------------------------

    def record_request(self, key_pk: int, model_scope: str, now: float) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO llm_rate_usage (key_id, model_id, kind, tokens, created_at) "
                "VALUES (?, ?, 'request', 0, ?)",
                (key_pk, model_scope, now),
            )
        finally:
            conn.close()

    def record_tokens(
        self, key_pk: int, model_scope: str, tokens: int, now: float, month: str
    ) -> None:
        conn = self._conn()
        try:
            with transaction(conn):
                conn.execute(
                    "INSERT INTO llm_rate_usage (key_id, model_id, kind, tokens, created_at) "
                    "VALUES (?, ?, 'tokens', ?, ?)",
                    (key_pk, model_scope, tokens, now),
                )
                conn.execute(
                    "INSERT INTO llm_key_monthly_usage (key_id, month, requests, tokens) "
                    "VALUES (?, ?, 1, ?) ON CONFLICT(key_id, month) DO UPDATE SET "
                    "requests = requests + 1, tokens = tokens + excluded.tokens",
                    (key_pk, month, tokens),
                )
        finally:
            conn.close()

    def window_usage(self, key_pk: int, model_scope: str, since: float) -> tuple[int, int]:
        """``(requests, tokens)`` recorded for the key and model since ``since``."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(kind = 'request'), 0) AS r, "
                "COALESCE(SUM(CASE WHEN kind = 'tokens' THEN tokens ELSE 0 END), 0) AS t "
                "FROM llm_rate_usage WHERE key_id = ? AND model_id = ? AND created_at >= ?",
                (key_pk, model_scope, since),
            ).fetchone()
        finally:
            conn.close()
        return int(row["r"]), int(row["t"])

    def monthly_usage(self, key_pk: int, month: str) -> tuple[int, int]:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT requests, tokens FROM llm_key_monthly_usage WHERE key_id = ? AND month = ?",
                (key_pk, month),
            ).fetchone()
        finally:
            conn.close()
        return (int(row["requests"]), int(row["tokens"])) if row else (0, 0)

    def prune_usage(self, before: float) -> None:
        conn = self._conn()
        try:
            conn.execute("DELETE FROM llm_rate_usage WHERE created_at < ?", (before,))
        finally:
            conn.close()

    # --- attempt log (analytics) -----------------------------------------

    def log_attempt(
        self,
        *,
        key_pk: int | None,
        key_label: str,
        provider: str,
        model_id: str,
        operation: str,
        success: bool,
        error_category: str | None,
        latency_ms: int,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        created_at: str,
    ) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO llm_attempts (key_id, key_label, provider, model_id, operation, "
                "success, error_category, latency_ms, input_tokens, output_tokens, cost_usd, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    key_pk,
                    key_label,
                    provider,
                    model_id,
                    operation,
                    1 if success else 0,
                    error_category,
                    max(0, latency_ms),
                    max(0, input_tokens),
                    max(0, output_tokens),
                    max(0.0, cost_usd),
                    created_at,
                ),
            )
        finally:
            conn.close()

    def attempt_stats(self, since: str | None) -> dict[str, list[dict[str, Any]]]:
        """Attempts since ``since`` (ISO UTC, or all) grouped per key, per model and per error."""
        where = "WHERE a.created_at >= ?" if since else ""
        args: tuple[Any, ...] = (since,) if since else ()
        measures = (
            "COUNT(*) AS requests, COALESCE(SUM(a.success), 0) AS successes, "
            "COALESCE(AVG(a.latency_ms), 0) AS avg_latency_ms, "
            "COALESCE(SUM(a.input_tokens), 0) AS input_tokens, "
            "COALESCE(SUM(a.output_tokens), 0) AS output_tokens, "
            "COALESCE(SUM(a.cost_usd), 0) AS cost_usd"
        )
        conn = self._conn()
        try:
            keys = conn.execute(
                # The live key row wins for the label; the logged label covers deleted keys.
                f"SELECT a.key_id AS key_id, COALESCE(k.label, MAX(a.key_label)) AS label, "
                f"MAX(a.provider) AS provider, {measures} "
                f"FROM llm_attempts a LEFT JOIN llm_api_keys k ON k.id = a.key_id "
                f"{where} GROUP BY a.key_id ORDER BY requests DESC",
                args,
            ).fetchall()
            models = conn.execute(
                f"SELECT a.provider AS provider, a.model_id AS model, {measures} "
                f"FROM llm_attempts a {where} GROUP BY a.provider, a.model_id "
                f"ORDER BY cost_usd DESC, requests DESC",
                args,
            ).fetchall()
            errors = conn.execute(
                f"SELECT COALESCE(a.error_category, 'unknown') AS category, COUNT(*) AS count "
                f"FROM llm_attempts a {where}{' AND' if where else 'WHERE'} a.success = 0 "
                f"GROUP BY category ORDER BY count DESC",
                args,
            ).fetchall()
        finally:
            conn.close()
        return {
            "keys": [dict(row) for row in keys],
            "models": [dict(row) for row in models],
            "errors": [dict(row) for row in errors],
        }
