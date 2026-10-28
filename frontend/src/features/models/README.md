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
- A toggle (single, or a provider's select/deselect all) moves only the models whose state changed: disabled to the end of the whole chain, enabled right after the last enabled entry (hidden models count). Old chains, drag and the rank box are not corrected.
- "Primary" is the first enabled entry in the whole chain (`primaryIndex`); it can be a hidden model, in which case no row shows it.
- Search (`filterEntries`) is view-only state in `ModelsScreen`: it never enters the staged chain, ranks stay whole-chain positions, matching groups open, group headers, drag and the rank box are off, and select/deselect all act on the matches only.

## Side Effects
- Network calls: create (single/bulk), update, delete, test models; `saveFallback`.

## Verification
- Run: `npm test` (from `frontend/`)
