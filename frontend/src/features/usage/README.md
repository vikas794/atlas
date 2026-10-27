# features/usage

## Overview
Usage dashboard of LLM usage. Not a route; rendered inside `SettingsScreen`.

## Interface
- `UsageDashboard` - uses only `getUsageAggregate` from `lib/api`; takes an optional `className` prop and keeps its filter state locally as `UsageQueryParams`.

## Invariants
- Read-only view; query parameters follow `UsageQueryParams`.

## Side Effects
- Network reads only.

## Verification
- Run: `npm test` (from `frontend/`)
