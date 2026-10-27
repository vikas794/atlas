# features/models

## Overview
Model catalog and fallback-chain editor (route `/models`).

## Interface
- `ModelsScreen` - route component; stages ordering/enable changes and saves the fallback chain.
- `ModelTable` - provider-grouped, collapsible table with select/deselect all per provider.
- `ModelDialog` - add/edit model and bulk import dialog.

- `ModelsScreen` also calls `getRateLimitUsage` (quota display); `ModelDialog` calls `getAvailableModels` for the bulk-import picker.

## Invariants
- Chain ordering, staging and provider grouping logic lives in `lib/model-chain`; the UI must not duplicate it.
- Providers are filtered to those with an enabled key (`withEnabledKey`).

## Side Effects
- Network calls: create (single/bulk), update, delete, test models; `saveFallback`.

## Verification
- Run: `npm test` (from `frontend/`)
