"""Admin CLI for the Atlas SQLite pipeline store.

Usage:
    python -m backend.storage.cli init
    python -m backend.storage.cli status
    python -m backend.storage.cli purge [--retention-days N]
    python -m backend.storage.cli delete-legacy [--dry-run]
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _settings() -> dict:
    from backend.storage.settings import get_settings

    return get_settings()


def _repository():
    from backend.storage.repository import RunRepository

    settings = _settings()
    return RunRepository(settings["database_path"], settings["artifact_root"])


def cmd_init(_args) -> int:
    repo = _repository()
    print(f"Storage initialized at {repo.db_path}")
    print(f"Artifact root: {repo.artifact_root}")
    return 0


def cmd_status(_args) -> int:
    repo = _repository()
    stats = repo.stats()
    print(f"Database: {repo.db_path}")
    print(f"Artifact root: {repo.artifact_root}")
    print("Runs:")
    for status, count in sorted(stats["runs"].items()):
        print(f"  {status}: {count}")
    print("Generation jobs:")
    for status, count in sorted(stats["generation_jobs"].items()):
        print(f"  {status}: {count}")
    print(f"Cache entries: {stats['cache_entries']}")
    print(f"Expired cache entries: {stats['expired_cache_entries']}")
    return 0


def cmd_purge(args) -> int:
    settings = _settings()
    retention = args.retention_days or settings["cleanup_retention_days"]
    repo = _repository()
    result = repo.purge_expired(retention_days=retention)
    print(f"Removed {result['removed_cache_entries']} expired cache entries")
    print(f"Removed runs: {', '.join(result['removed_runs']) or 'none'}")
    print(f"Removed orphan artifact folders: {', '.join(result['removed_orphan_folders']) or 'none'}")
    return 0


def _legacy_folders() -> list[Path]:
    return sorted(
        path for path in REPO_ROOT.iterdir()
        if path.is_dir() and re.fullmatch(r"pipeline_output_\d+", path.name)
    )


def cmd_delete_legacy(args) -> int:
    folders = _legacy_folders()
    if not folders:
        print("No legacy pipeline_output_* folders found.")
        return 0
    for folder in folders:
        print(f"{'[dry-run] would delete' if args.dry_run else 'Deleting'} {folder}")
        if not args.dry_run:
            shutil.rmtree(folder, ignore_errors=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="atlas-storage",
        description="Admin commands for the Atlas SQLite pipeline store.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Create/migrate the database.")
    subparsers.add_parser("status", help="Show run/cache/job status.")

    purge = subparsers.add_parser("purge", help="Purge expired cache and aged runs/artifacts.")
    purge.add_argument("--retention-days", type=int, default=None)

    legacy = subparsers.add_parser(
        "delete-legacy", help="Delete legacy pipeline_output_* folders (explicit only)."
    )
    legacy.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    command = args.command
    if command == "init":
        return cmd_init(args)
    if command == "status":
        return cmd_status(args)
    if command == "purge":
        return cmd_purge(args)
    if command == "delete-legacy":
        return cmd_delete_legacy(args)
    parser.error(f"Unknown command: {command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
