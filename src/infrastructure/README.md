# infrastructure

## Overview
Adapters implementing domain ports for external systems: SQLite storage, YouTube, transcript download, LLM notes generation and Google Drive publishing.

## Subdirectories & Boundaries
- google: [./google/README.md](./google/README.md) - Drive/Docs OAuth, exporter and playlist publisher.
- llm: [./llm/README.md](./llm/README.md) - LiteLLM notes generator, prompts, pricing and model registry (see also [./llm/registry/README.md](./llm/registry/README.md)).
- storage: [./storage/README.md](./storage/README.md) - SQLite connection, migrations, repositories and usage ledger.
- transcript: [./transcript/README.md](./transcript/README.md) - transcript providers (yt-dlp).
- youtube: [./youtube/README.md](./youtube/README.md) - YouTube Data API playlist provider.
