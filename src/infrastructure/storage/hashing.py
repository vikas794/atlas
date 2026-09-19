"""Filesystem helpers for the managed artifact directory, plus cache-key
normalization and hashing helpers (merged from the former backend cache
helper module)."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path


def atomic_write(path: str | Path, content: str | bytes) -> None:
    """Write content to a temp file then atomically replace ``path``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    data = content if isinstance(content, bytes) else content.encode("utf-8")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_text(content: str) -> str:
    return sha256_bytes(content.encode("utf-8"))


def sha256_file(path: str | Path) -> str | None:
    path = Path(path)
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_size(path: str | Path) -> int | None:
    path = Path(path)
    if not path.exists():
        return None
    return path.stat().st_size


def json_dumps_stable(obj) -> str:
    """Deterministic JSON serialization used for content and state hashing."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


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
