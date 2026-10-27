# features/settings

## Overview
Settings screen (route `/settings`): app configuration and Google Drive authorization.

## Interface
- `SettingsScreen` - route component composing the panels and `UsageDashboard` (from `features/usage`).
- `AppConfigPanel` - edit/reset app settings.
- `GoogleAuthPanel` - upload Drive credentials and start authentication; reads connection state with `getDriveStatus`.

## Invariants
- Settings are server-owned; the UI edits via `getSettings`/`updateSettings`/`resetSettings`.

## Side Effects
- Network calls; credential upload and Drive OAuth initiation.

## Verification
- Run: `npm test` (from `frontend/`)
