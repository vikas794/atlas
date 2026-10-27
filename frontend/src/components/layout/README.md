# components/layout

## Overview
App chrome rendered by `App.tsx` around every routed screen.

## Interface
- `TopBar` - floating glass-pill navigation bar: wordmark, `NAV_LINKS` (Models, Keys, Diagnostics, Settings) and `ThemeToggle`.
- `ThemeToggle` - three-button radiogroup (System, Light, Dark) that sets the theme preference directly via `lib/theme`.

## Invariants
- Theme state lives in `lib/theme` (persisted in `localStorage`); this directory only reads and sets it.
- Colors come from global tokens, never hardcoded (docs/design.md).

## Side Effects
- `ThemeToggle` writes the theme preference to `localStorage` and updates the document theme.

## Verification
- Run: `npm test` (from `frontend/`)
