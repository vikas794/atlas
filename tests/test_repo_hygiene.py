"""Secrets and local data are gitignored; source folders are not swallowed by broad patterns."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from src.config.settings import repo_root

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

MUST_IGNORE = [
    ".env",
    ".env.local",
    "credentials.json",
    "token.json",
    "client_secret_123.json",
    "secrets/token.json",
    "data/atlas.sqlite3",
    "data/atlas.sqlite3.pre-0005.bak",
    "logs/atlas.log",
    "logs/atlas.log.1",
    ".runtime/backend.pid",
    "transcripts/abc.en.srt",
    ".pytest_tmp/x/y.txt",
    "frontend/dist/index.html",
    "frontend/node_modules/react/index.js",
    "task.md",
]
MUST_TRACK = [
    ".env.example",
    ".claude/launch.json",
    "src/configs/config.yaml",
    "src/infrastructure/storage/data/fixtures.py",
    "frontend/src/features/summaries/view.tsx",
    "frontend/src/lib/output-format.ts",
    "src/prompts/templates/notes.yaml",
]


def ignored(paths: list[str]) -> set[str]:
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", *paths],
        cwd=repo_root(),
        capture_output=True,
        text=True,
    )
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def test_secrets_and_local_data_are_ignored() -> None:
    assert sorted(set(MUST_IGNORE) - ignored(MUST_IGNORE)) == []


def test_source_paths_are_not_ignored() -> None:
    assert sorted(ignored(MUST_TRACK)) == []
