# features/playlists

## Overview
Playlist ingestion, processing progress, and per-video summary review/publish (routes `/`, `/playlists/:id`, `/playlists/:id/review/:videoId`).

## Interface
- `PlaylistsHome` - playlist list and ingest form.
- `PlaylistDetail` - video table, stage filters, process/retry/approve/publish actions.
- `ReviewScreen` - edit and approve a video's notes; guards unsaved edits with `useBlocker`, keeps the focus-mode flag via `useStoredFlag('atlas.review.focus')` and loads the notes prompt with `getNotesPrompt`.
- `ClearTranscriptDialog` - confirm transcript cleanup.
- `usePublishJob`, `useRetryVideo` - mutation hooks for publish and retry flows.

## Invariants
- Polling cadence comes from `lib/polling`; busy detection must stay consistent with pipeline stages.
- Cache invalidation uses `invalidatePlaylist` / `invalidateVideo` in places and `queryKeys` directly (`invalidateQueries`/`removeQueries`) in others.

## Side Effects
- Network calls to the playlist/video API; publishing writes to Google Drive via the backend.
- Periodic polling while videos are busy.

## Verification
- Run: `npm test` (from `frontend/`)
