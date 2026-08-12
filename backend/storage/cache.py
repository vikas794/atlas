"""Cache-key normalization and hashing helpers."""

from __future__ import annotations

import hashlib
import re

from backend.storage.artifacts import json_dumps_stable


def normalize_query(query: str) -> str:
    """Normalize a search query for cache identity."""
    return re.sub(r"\s+", " ", query.strip().lower())


def settings_hash(settings: dict) -> str:
    """Stable hash of generation settings (cache identity for derived artifacts)."""
    return hashlib.sha256(json_dumps_stable(settings).encode("utf-8")).hexdigest()


def cache_key(
    kind: str,
    normalized_query: str,
    max_videos: int | None = None,
    transcript_language: str | None = None,
) -> str:
    """Search-cache identity: kind + normalized query + search-affecting inputs."""
    payload = f"{kind}|{normalized_query}|{max_videos}|{transcript_language}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
