# features

## Overview
One directory per screen/domain. Routed screens are lazily loaded by `routes.tsx`; `usage` has no route of its own (its dashboard renders inside the settings screen).

## Subdirectories & Boundaries
- diagnostics: [./diagnostics/README.md](./diagnostics/README.md) - health and recent errors.
- keys: [./keys/README.md](./keys/README.md) - LLM API key management.
- models: [./models/README.md](./models/README.md) - model catalog and fallback chain.
- playlists: [./playlists/README.md](./playlists/README.md) - ingest, progress, review, publish.
- settings: [./settings/README.md](./settings/README.md) - app config and Google auth.
- usage: [./usage/README.md](./usage/README.md) - usage dashboard (rendered by settings, not routed).
