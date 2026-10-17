"""Persistence for UI-edited setting overrides (the ``app_settings`` table)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.infrastructure.storage.db import connect, initialize, now_iso, transaction


class SqlSettingsRepository:
    """One row per overridden setting; the value is stored as JSON so types survive."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        initialize(self._db_path)

    def load_all(self) -> dict[str, Any]:
        conn = connect(self._db_path)
        try:
            rows = conn.execute("SELECT key, value_json FROM app_settings").fetchall()
        finally:
            conn.close()
        return {row["key"]: json.loads(row["value_json"]) for row in rows}

    def replace(self, values: dict[str, Any]) -> None:
        """Make ``values`` the complete set of overrides, atomically."""
        stamp = now_iso()
        conn = connect(self._db_path)
        try:
            with transaction(conn):
                conn.execute("DELETE FROM app_settings")
                conn.executemany(
                    "INSERT INTO app_settings (key, value_json, updated_at) VALUES (?, ?, ?)",
                    [(key, json.dumps(value), stamp) for key, value in values.items()],
                )
        finally:
            conn.close()

    def clear(self) -> None:
        self.replace({})
