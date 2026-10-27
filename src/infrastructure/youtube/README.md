# youtube

## Overview
YouTube Data API v3 adapter that lists the videos of a playlist and resolves its title. Maps API errors to domain-level failures.

## Interface
- `YouTubePlaylistProvider` (`playlist.py`): `fetch_playlist_videos(playlist_id, max_videos=None)` (dense 1-based positions, placeholders skipped), `get_playlist_title(playlist_id)` (returns `"Untitled Playlist"` when there is no title). Constructor `api_key_env` (from `settings.youtube_api_key_env`); `service_factory` is test-only.
- Constants: `PAGE_SIZE = 50`, `DEFAULT_API_KEY_ENV = "YOUTUBE_API_KEY"`.
- Package `__init__` re-exports the provider.

## Invariants
- Placeholder titles ("Private video", "Deleted video") are treated as unavailable entries.
- Error reasons are classified into: playlist unavailable (HTTP 404 or `invalid` and similar reasons, `PipelineError(PLAYLIST_UNAVAILABLE)`), API key rejected (`ConfigurationError`), quota exhausted (`PipelineError(RATE_LIMITED)`). Other API or network failures become `ProviderError(provider="youtube")`.
- The API key comes from the environment variable named by `api_key_env` (default `YOUTUBE_API_KEY`); several comma-separated keys are supported.
- Multi-key: `_execute` rotates a shared `KeyPool`, disables a rejected key, puts an out-of-quota key on `LONG_COOLDOWN_SECONDS`, and raises `PipelineError(RATE_LIMITED)` when all keys are out of quota. Keys added to `.env` are picked up without restart.

## Side Effects
- Network calls to the YouTube Data API (consumes quota). No disk writes.

## Verification
- Run: `pytest` (from repo root)
