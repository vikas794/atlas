# transport Blueprint

## System Overview
Delivery layer that exposes Atlas use cases to clients. `__init__.py` exports the `http` subpackage. Transports depend on application ports and infrastructure; nothing inward depends on them.

## Local Interface & Invariants
- `__init__.py` exports `http`; no other local symbols.

## Subdirectories & System Boundaries
- http: [./http/README.md](./http/README.md) - HTTP transport, currently FastAPI ([./http/fastapi/README.md](./http/fastapi/README.md)).

## Global Invariants
- App assembly (`include_router`, `RequestLoggingMiddleware`, the `/api/diagnostics` route) lives in `backend/main.py`, not here.
- Transport code contains no business logic; it validates input, calls use cases, and maps results and errors to API shapes (PRD section 16).
- All error bodies use the `{code, message, details}` envelope with secrets redacted.

## Global Side Effects
- SQLite database, Google Drive/OAuth, YouTube and LLM provider network calls, and the background worker, all reached via `http/fastapi/dependencies.py`.

## System Verification
- Run: `pytest` (from repo root)
