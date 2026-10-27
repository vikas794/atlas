# lib

## Overview
Framework-light logic shared by components and features: the typed API client, error model, query configuration, domain derivations (badges, progress, model chain) and small hooks.

## Interface
- API: `api.ts` (`request` plus one function per backend endpoint, e.g. `getPlaylists`, `publishPlaylist`, `getLlmKeys`, `getDiagnostics`), `types.ts` (request/response types), `errors.ts` (`ApiError`, `toApiError`, `errorMessage`, `failureMessage`, `isNotFound`, `networkError`, `invalidResponseError`, `NETWORK_ERROR_MESSAGE`, `INVALID_RESPONSE_MESSAGE`); `api.ts` also exports `UsageQueryParams`, `UsageAggregateResponse`.
- Query: `query-client.ts` (`createQueryClient`, `shouldRetry`), `query-keys.ts` (`queryKeys`, `invalidatePlaylist`, `invalidateVideo`), `polling.ts` (`pollInterval`, `playlistIsBusy`, `videosAreBusy`, `FAST_POLL_MS`, `SLOW_POLL_MS`), `video-queries.ts` (`fetchPlaylistVideos`, `fetchAllPlaylistVideos`, `MAX_PAGE_SIZE`), `jobs.ts` (`publishPhase`, `regeneratePhase`, `describePublish`, types `JobWatch`, `JobPhase`).
- Derivations: `stage-badge.ts` (`stageToBadge`, `videoBadge`, `videoDetail`, `isRetryScheduled`, `isRetriesExhausted`, `failureLabel`, `attemptLabel`, `exhaustedNote`, `FAILURE_REASONS`, `PERMANENT_NOTE`, types `BadgeSpec`, `RetryFields`, `VideoDetailSpec`), `key-status-badge.ts` (`keyBadge`, `keyStatusToBadge`, `activeCooldowns`, `longestCooldown`, `autoDisabledNote`), `progress.ts` (`progressSegments`, `STAGE_FILTERS`, `filterCount`, types), `model-chain.ts` (`groupByProvider`, `toStaged`, `toUpdates`, `quotaSummary`, `moveEntry`, `withEnabledKey`, `applyStaged`, `setEnabled`, `clampRank`, `chainChanged`, `windowFraction`, `quotaFraction`, `formatContext`, `blockedReason`, types), `key-groups.ts` (`groupKeys`), `review-nav.ts` (`findNextNeedingReview`), `tone.ts` (`Tone`, `TONE_BADGE`, `TONE_TEXT`, `SEGMENT_COLOR`).
- Formatting/validation: `format.ts` (`formatRelativeTime`, `pluralize`, ...), `playlist-url.ts` (`validatePlaylistUrl`), `utils.ts` (`cn`), `row-keys.ts` (`onRowKeyDown`).
- UI state: `theme.ts` (`initTheme`, `setThemePreference`, `getThemePreference`, `getSystemTheme`, `getResolvedTheme`, `nextThemePreference`, `THEME_PREFERENCES`, types `ThemePreference`, `ResolvedTheme`), `motion.ts` (`motionAllowed`), hooks `useDebouncedValue`, `useNow`, `useReveal`, `useStoredFlag`.

## Invariants
- All HTTP goes through `request` in `api.ts`; base URL is `VITE_API_BASE_URL` (default same-origin, Vite dev proxy).
- Non-2xx and network/parse failures surface as `ApiError`.
- Badge, stage and failure-reason mapping is centralized here and must match the PRD pipeline states.
- Pure helpers stay side-effect free and are unit tested alongside (`*.test.ts`).

## Side Effects
- `api.ts` performs `fetch` calls to the backend.
- `theme.ts` and `use-stored-flag.ts` read/write `localStorage`; `theme.ts` mutates document theme attributes.

## Verification
- Run: `npm test` (from `frontend/`)
