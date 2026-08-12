"""Resolve storage settings from environment variables and config.yaml."""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.utils import get_config  # noqa: E402


def get_settings() -> dict:
    """Return effective storage configuration (env overrides config.yaml)."""
    db_path = os.getenv("ATLAS_DB_PATH") or get_config("storage.database_path", "data/atlas.sqlite3")
    artifact_root = os.getenv("ATLAS_ARTIFACT_ROOT") or get_config("storage.artifact_root", "data/artifacts")
    cache_ttl = os.getenv("ATLAS_CACHE_TTL_DAYS") or get_config("storage.cache_ttl_days", 30)
    retention = os.getenv("ATLAS_RETENTION_DAYS") or get_config("storage.cleanup_retention_days", 90)

    if not os.path.isabs(db_path):
        db_path = str(REPO_ROOT / db_path)
    if not os.path.isabs(artifact_root):
        artifact_root = str(REPO_ROOT / artifact_root)

    return {
        "database_path": db_path,
        "artifact_root": artifact_root,
        "cache_ttl_days": int(cache_ttl),
        "cleanup_retention_days": int(retention),
    }
