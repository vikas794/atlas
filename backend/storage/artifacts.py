"""Filesystem helpers for the managed artifact directory."""

from __future__ import annotations

import hashlib
import json
import os
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
