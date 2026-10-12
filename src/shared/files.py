"""Tiny file-system helpers shared across layers."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

logger = logging.getLogger(__name__)


def delete_files(paths: Iterable[str], *, root: str | Path | None = None) -> int:
    """Delete the given files, ignoring ones that are already gone.

    Args:
        paths: File paths to delete.
        root: When given, only files inside this directory are deleted (a stored
            path is never trusted to point anywhere else).

    Returns:
        How many files were actually removed. Failures are logged and skipped, so a
        locked file can never block a database cleanup.
    """
    resolved_root = Path(root).resolve() if root is not None else None
    removed = 0
    for raw in paths:
        path = Path(raw).resolve()
        if resolved_root is not None and not path.is_relative_to(resolved_root):
            logger.warning("Refusing to delete a file outside %s: %s", resolved_root, path.name)
            continue
        try:
            path.unlink()
            removed += 1
        except FileNotFoundError:
            continue
        except OSError:
            logger.warning("Could not delete artifact %s", path.name, exc_info=False)
    return removed


__all__ = ["delete_files"]
