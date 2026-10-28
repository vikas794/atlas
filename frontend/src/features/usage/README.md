# features/usage

## Overview
Usage dashboard of LLM usage. Not a route; rendered inside `SettingsScreen`.

## Interface
- `UsageDashboard` - uses `getUsageAggregate` from `lib/api`; takes an optional `className` prop and keeps its filter state locally as `UsageQueryParams`. Renders `KeyAnalytics` below the aggregates.
- `KeyAnalytics` (`key-analytics.tsx`) - `getKeyAnalytics(range)` (`GET /api/usage/keys`): per-key success/error/latency/cost table, a cost-per-model table with totals, and errors by category. Local range state (`24h`, `7d`, `30d`, `all`).
- `usage-ui.tsx` / `usage-format.ts` - shared `Stat`, `TableShell`, `Head` and number/cost formatters.

## Invariants
- Read-only view; query parameters follow `UsageQueryParams`.

## Side Effects
- Network reads only.

## Verification
- Run: `npm test` (from `frontend/`)
