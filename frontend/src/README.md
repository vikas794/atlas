# src

## Overview
React 19 + TypeScript SPA entry. `main.tsx` loads fonts and styles, restores the theme, and mounts a data router (`createBrowserRouter(routes)`) inside `ErrorBoundary` and a TanStack Query provider. `App.tsx` renders the top bar, toast provider and routed outlet.

## Interface
- `routes` (`routes.tsx`) - route table; screens are lazy-loaded under a pathless route with `RouteErrorBoundary`: `/`, `/playlists/:id`, `/playlists/:id/review/:videoId`, `/models`, `/keys`, `/settings`, `/diagnostics`, `*` (NotFound).
- `index.css`, `tokens.css` - the single global stylesheet and design tokens.
- Subdirectories:
  - [components](./components/README.md) - shared presentational components.
  - [features](./features/README.md) - per-screen modules.
  - [lib](./lib/README.md) - API client, query setup, derivations, hooks.
  - `features/usage` - `UsageDashboard`, not routed; rendered inside `SettingsScreen` (see [features](./features/README.md)).
  - `test/` - test harness only (not documented).

## Invariants
- Routes are data (not `<Routes>`) so `useBlocker` works in the Review screen.
- Components must not hardcode colors; use tokens.

## Side Effects
- `main.tsx` calls `initTheme()` before first paint and mounts into the DOM.

## Verification
- Run: `npm test` (from `frontend/`)
