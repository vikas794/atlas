"""Hexagonal layering is enforced by a test, not by convention (PRD section 21)."""

from __future__ import annotations

import re
from pathlib import Path

from src.config.settings import repo_root

IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+(src\.[\w.]+)", re.MULTILINE)


def imports_of(path: Path) -> set[str]:
    return set(IMPORT_RE.findall(path.read_text(encoding="utf-8")))


def python_files(*parts: str) -> list[Path]:
    return sorted((repo_root() / "src" / Path(*parts)).rglob("*.py"))


def test_the_domain_depends_on_nothing_outward() -> None:
    offenders = {
        str(path.relative_to(repo_root())): sorted(
            m for m in imports_of(path)
            if m.startswith(("src.infrastructure", "src.application", "src.transport", "src.config"))
        )
        for path in python_files("domain")
    }

    assert {k: v for k, v in offenders.items() if v} == {}


def test_use_cases_and_the_worker_depend_on_ports_not_adapters() -> None:
    files = python_files("application")
    assert files, "expected application modules"

    offenders = {
        str(path.relative_to(repo_root())): sorted(
            m for m in imports_of(path) if m.startswith(("src.infrastructure", "src.transport"))
        )
        for path in files
    }

    assert {k: v for k, v in offenders.items() if v} == {}


def test_the_concrete_repository_is_only_named_in_the_composition_root() -> None:
    offenders = []
    for path in python_files("application") + python_files("transport") + python_files("domain"):
        if path.name == "dependencies.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "storage.playlist_repo" in text or re.search(r"\bPlaylistRepository\b", text):
            offenders.append(str(path.relative_to(repo_root())))

    assert offenders == []


def test_the_notes_port_does_not_build_adapters() -> None:
    source = (repo_root() / "src" / "domain" / "interfaces" / "notes_generator.py").read_text(
        encoding="utf-8"
    )

    assert not re.search(r"^\s*(from|import)\s+src\.infrastructure", source, re.MULTILINE)
    assert "def build_notes_generator" not in source
