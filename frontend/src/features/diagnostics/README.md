# features/diagnostics

## Overview
Diagnostics screen (route `/diagnostics`) showing system health and recent errors.

## Interface
- `DiagnosticsScreen` - lazy-loaded route component. Uses `getDiagnostics` and `clearRecentErrors` from `lib/api`. Polls at `SLOW_POLL_MS` (`lib/polling`); the clear mutation writes the result with `setQueryData` instead of invalidating.

## Invariants
- Server data is read through TanStack Query with keys from `lib/query-keys`.

## Side Effects
- Network calls to the diagnostics API; clearing recent errors mutates backend state.

## Verification
- Run: `npm test` (from `frontend/`)
