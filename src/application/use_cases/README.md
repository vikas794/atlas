# application/use_cases

## Overview
Package of application use cases; `__init__.py` is the package marker (its docstring notes the legacy search-run and quiz orchestrators were removed). Playlist-pipeline use cases live in the subpackage.

## Interface
- [./playlist/README.md](./playlist/README.md) - ingest, process, action, and publish use cases.

## Invariants
- Use cases depend on domain ports, models and services, plus `src.shared` helpers (`redaction`, `text`, `files`); never on infrastructure or transport.

## Side Effects
- None directly; see subpackage.

## Verification
- Run: `pytest`
