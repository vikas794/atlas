# src

## Overview
The Atlas pipeline code, organized in layers: `domain` (entities, ports, pure rules) at the center, `application` (use cases and the background worker) around it, and `infrastructure` (adapters for SQLite, YouTube, yt-dlp, LLMs, Google) and `transport` (the HTTP API) at the edge. `config` and `shared` are used by the outer layers (`domain` imports neither). `configs/config.yaml` and `prompts/*.yaml` are data files, not code.

## Subdirectories & Boundaries
- application: [./application/README.md](./application/README.md) - `PlaylistWorker` and the playlist use cases.
- config: [./config/README.md](./config/README.md) - layered settings (yaml, saved UI settings, environment).
- domain: [./domain/README.md](./domain/README.md) - models, ports, pure services, exceptions.
- infrastructure: [./infrastructure/README.md](./infrastructure/README.md) - Google, LLM, storage, transcript and YouTube adapters.
- shared: [./shared/README.md](./shared/README.md) - key rotation, redaction, logging, file and text helpers.
- transport: [./transport/README.md](./transport/README.md) - FastAPI routers, schemas, error envelope, middleware.
- configs: `./configs/config.yaml` - default settings (no code).
- prompts: `./prompts/notes_v1.yaml`, `notes_v1_1.yaml`, `notes_v1_2.yaml` - versioned note-generation prompts (no code).

## Local Interface & Invariants
- `src/__init__.py` is a package marker only.
- Dependencies point inward: `domain` imports nothing from the other layers; `application` imports `domain` (plus `src.shared` helpers) and never infrastructure or transport; `infrastructure` and `transport` implement or call those ports (layering rules are checked by `tests/test_architecture.py`).

## Verification
- Run: `uv run pytest -q`
