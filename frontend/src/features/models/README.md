# features/models

## Overview
Model catalog and fallback-chain editor (route `/models`).

## Interface
- `ModelsScreen` - route component; stages ordering/enable changes and saves the fallback chain.
- `ModelTable` - provider-grouped, collapsible table with select/deselect all (enable) per provider, row and group checkboxes for bulk actions, and a row Hide/Unhide action.
- `ServingOrder` - the "Serving order" strip: draggable chips of the models that can serve now (`isServing`: enabled with a usable key).
- `ModelDialog` - add/edit model and bulk import dialog.

- `ModelsScreen` also calls `getRateLimitUsage` (quota display); `ModelDialog` calls `getAvailableModels` for the bulk-import picker.

## Invariants
- Chain ordering, staging and provider grouping logic lives in `lib/model-chain`; the UI must not duplicate it.
- Providers are filtered to those with an enabled key (`listedEntries`), unless "Show no-key models" is on; those rows show dimmed with a "No keys" badge and are excluded from the stat cards either way.
- A toggle (single, or a provider's select/deselect all) moves only the models whose state changed: disabled to the end of the whole chain, enabled right after the last enabled entry (hidden models count). Old chains, drag and the rank box are not corrected.
- "Primary" is the first enabled entry in the whole chain (`primaryIndex`); it can be a hidden model, in which case no row shows it.
- Search (`filterEntries`) is view-only state in `ModelsScreen`: it never enters the staged chain, ranks stay whole-chain positions, matching groups open, group headers, drag and the rank box are off, and select/deselect all act on the matches only.

- Bulk Hide / Delete (ticked rows, a bar at the bottom like the Keys batch bar) are immediate server calls, not staged: they never touch the staged chain. Ticks only count while the row is listed.
- A hidden model stays registered so "Add all" skips it (the `UNIQUE (provider, model_id)` check), but it is switched off and not listed (`listedEntries`) unless "Show hidden" is on; there it can be unhidden. `setEnabled` and `applyStaged` never switch a hidden model on. Hiding moves the model to the end of the chain and renumbers the rest (server `set_hidden`; `applyStaged` does the same for staged edits), so visible ranks have no gaps. Delete removes the row, so "Add all" can re-add it.

- The Serving order strip reorders the whole chain across providers (`reorderServing`: the serving models swap among the slots they occupy; disabled, no-key and hidden entries keep their positions). A drop saves at once (`saveFallback`, optimistic, rolled back with an error toast on failure), unlike table edits. It is built from the whole chain, so search and the Show hidden / no-key boxes never change it, and it is locked (no drag) while staged table edits exist or a save is in flight. Pointer drag only, no keyboard reordering.

## Side Effects
- Network calls: create (single/bulk), update, delete (single/bulk), hide/unhide (bulk), test models; `saveFallback`.

## Verification
- Run: `npm test` (from `frontend/`)
