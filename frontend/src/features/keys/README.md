# features/keys

## Overview
LLM API key management screen (route `/keys`).

## Interface
- `KeysScreen` - route component; lists, checks and manages keys.
- `KeyList` - grouped key rows (grouping via `lib/key-groups`).
- `KeyFormDialog`, `RevealKeyDialog` - create/edit and reveal-secret dialogs.

## Invariants
- Key secrets are only displayed through the explicit reveal flow (`revealLlmKey`).
- Row badges come from `lib/key-status-badge`.

## Side Effects
- Network calls: create/update/delete/reveal/check keys, clear cooldowns.

## Verification
- Run: `npm test` (from `frontend/`)
