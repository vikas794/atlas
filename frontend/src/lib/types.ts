/** Shapes returned by the Atlas backend. */

/**
 * Per-video pipeline stage. Mirrors `src/domain/models/playlist.py::Stage`.
 * Declared as a string union rather than an enum so it matches the wire format
 * exactly and survives `as const` narrowing without a runtime import.
 */
export type Stage =
  | 'queued'
  | 'fetching_transcript'
  | 'transcript_ready'
  | 'generating_notes'
  | 'pending_review'
  | 'approved'
  | 'published'
  | 'failed'

/** Whether a failure is worth retrying. Mirrors backend `FailureKind`. */
export type FailureKind = 'transient' | 'permanent'

/** Specific failure reason. Mirrors backend `FailureType`. */
export type FailureType =
  | 'video_private'
  | 'video_deleted'
  | 'age_restricted'
  | 'region_blocked'
  | 'members_only'
  | 'transcripts_disabled'
  | 'no_usable_language'
  | 'transcript_too_large'
  | 'rate_limited'
  | 'network_error'
  | 'provider_error'
  | 'output_malformed'
  | 'playlist_unavailable'
  | 'invalid_url'
  | 'drive_error'

/**
 * Stages whose pipeline work is in flight. This single list defines the
 * "In progress" summary card, its table filter, the progress bar segment and
 * (together with `queued`) the 3s poll cadence, so none of them can disagree.
 */
export const ACTIVE_STAGES: readonly Stage[] = [
  'fetching_transcript',
  'transcript_ready',
  'generating_notes',
]

/** Every stage the backend accepts as a `stage=` filter (never send anything else). */
export const ALL_STAGES: readonly Stage[] = [
  'queued',
  'fetching_transcript',
  'transcript_ready',
  'generating_notes',
  'pending_review',
  'approved',
  'published',
  'failed',
]

/* --------------------------------------------------------------- Playlists */

export interface Playlist {
  id: number
  playlist_id: string
  source_url: string
  title: string
  video_count: number
  status: string
  drive_folder_id: string | null
  drive_index_doc_id: string | null
  created_at: string
  updated_at: string
  last_synced_at: string | null
  stage_counts: Record<Stage, number>
  /** Optional: last Drive publish error when `status` is `publish_failed`. */
  last_publish_error?: string | null
  /** Optional: result of the last async publish. */
  last_publish?: { created: number; updated: number; skipped: number } | null
}

/** Playlist `status` values the UI reacts to (others are shown as-is). */
export type PlaylistStatus = 'publishing' | 'publish_failed' | string

export interface PlaylistListResponse {
  playlists: Playlist[]
  total: number
}

export interface Video {
  id: number
  playlist_id: number
  youtube_video_id: string
  title: string
  position: number
  stage: Stage
  failure_type: FailureType | null
  failure_kind: FailureKind | null
  failure_reason: string | null
  retry_count: number
  next_retry_at: string | null
  drive_doc_id: string | null
  removed_upstream: boolean
  created_at: string
  updated_at: string
  /**
   * Optional fields the backend may add (the UI works without them).
   * `retries_exhausted` / `auto_retry`: whether automatic retries are over or
   * still scheduled. `max_auto_retries`: cap for the "attempt N/M" text.
   * `edited`: the notes were edited by the user.
   */
  retries_exhausted?: boolean
  auto_retry?: boolean
  max_auto_retries?: number | null
  edited?: boolean
}

export interface VideoListResponse {
  videos: Video[]
  total: number
  page: number
  page_size: number
}

export interface Summary {
  id: number
  video_id: number
  content: string
  provider: string | null
  model: string | null
  prompt_version: string | null
  generated_at: string
  edited: boolean
  approved: boolean
  approved_at: string | null
  published: boolean
  published_at: string | null
  published_digest: string | null
  /**
   * Optional: true when the stored notes differ from what was published.
   * When absent the UI compares SHA-256(content) with `published_digest`.
   */
  needs_republish?: boolean
}

export interface ActionResponse {
  status: string
  message: string
  count?: number | null
}

/** Sync publish (200) carries counts; async publish (202) only `status`/`message`. */
export interface PublishResponse {
  folder_id?: string
  index_doc_id?: string | null
  created?: number
  updated?: number
  skipped?: number
  status: string
  message?: string
}

/* ------------------------------------------------------------------- Drive */

export interface DriveStatusResponse {
  configured: boolean
  message: string
}

export interface DriveActionResponse {
  status: string
  message: string
}

/* ------------------------------------------------------------------- Usage */

export interface UsageProviderAggregate {
  provider: string
  total_requests: number
  total_tokens: number
  total_cost_usd: number
  models_used: string[]
}

export interface UsageOperationAggregate {
  operation: string
  total_requests: number
  total_tokens: number
  total_cost_usd: number
  avg_tokens_per_request: number
}

export interface UsageCacheAggregate {
  total_hits: number
  total_misses: number
  hit_rate: number
  by_kind: Record<string, [number, number]>
}

export interface UsageTimeRange {
  since: string | null
  until: string | null
}

export interface UsageAggregateResponse {
  total_requests: number
  total_input_tokens: number
  total_output_tokens: number
  total_tokens: number
  total_cost_usd: number
  cache_hit_rate: number
  by_provider: UsageProviderAggregate[]
  by_operation: UsageOperationAggregate[]
  cache_stats: UsageCacheAggregate | null
  time_range: UsageTimeRange | null
}
