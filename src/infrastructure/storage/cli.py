"""Admin CLI for the Atlas SQLite pipeline store.

Usage:
    python -m src.infrastructure.storage.cli init
    python -m src.infrastructure.storage.cli status
    python -m src.infrastructure.storage.cli delete-legacy [--dry-run]
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

from src.infrastructure.storage.db import connect, initialize

REPO_ROOT = Path(__file__).resolve().parents[3]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _settings() -> dict:
    from src.config import get_storage_settings, load_settings

    return get_storage_settings(load_settings())


def cmd_init(_args) -> int:
    settings = _settings()
    db_path = settings["database_path"]
    initialize(db_path)
    print(f"Storage initialized at {db_path}")
    return 0


def cmd_status(_args) -> int:
    settings = _settings()
    db_path = settings["database_path"]
    initialize(db_path)
    conn = connect(db_path)
    try:
        playlists_cnt = conn.execute("SELECT COUNT(*) FROM playlists").fetchone()[0]
        videos_cnt = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
        transcripts_cnt = conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0]
        summaries_cnt = conn.execute("SELECT COUNT(*) FROM summaries").fetchone()[0]
        usage_cnt = conn.execute("SELECT COUNT(*) FROM usage_ledger").fetchone()[0]
        print(f"Database: {db_path}")
        print(f"Playlists: {playlists_cnt}")
        print(f"Videos: {videos_cnt}")
        print(f"Transcripts: {transcripts_cnt}")
        print(f"Summaries: {summaries_cnt}")
        print(f"Usage ledger records: {usage_cnt}")
    finally:
        conn.close()
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
    subparsers.add_parser("status", help="Show pipeline status.")

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
    if command == "delete-legacy":
        return cmd_delete_legacy(args)
    parser.error(f"Unknown command: {command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
