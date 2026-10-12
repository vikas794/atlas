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
  | 'config_error'
  | 'internal_error'
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
  /** Only stages that have videos are present; read missing keys as 0. */
  stage_counts: Partial<Record<Stage, number>>
  /** Redacted reason of the last failed publish (set while `status` is `publish_failed`). */
  last_publish_error: string | null
}

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
  /** A transient failure is waiting for its scheduled automatic retry. */
  auto_retry: boolean
  /** A transient failure used up its automatic retries: manual Retry only. */
  retries_exhausted: boolean
  /** Primary key of the first occurrence when this row repeats an earlier video. */
  duplicate_of: number | null
  /** A "regenerate notes" request is queued or running. */
  regenerating: boolean
  /** Configured automatic-retry cap (denominator of "attempt N/M"). */
  max_auto_retries: number | null
  /**
   * The notes were hand-edited. Only the videos list endpoint fills this in;
   * single-video responses (approve, retry, regenerate) always send false.
   */
  edited: boolean
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
  /** The notes were edited after they were published (offer Re-publish). */
  needs_republish: boolean
}

export interface ActionResponse {
  status: string
  message: string
  count?: number | null
}

/**
 * Acknowledgement of POST /publish (HTTP 202). Publishing runs in the
 * background: the counts are always 0 here, the outcome is read from the
 * playlist's `status` (`publishing` -> `published` | `publish_failed`).
 */
export interface PublishResponse {
  folder_id: string
  index_doc_id: string | null
  created: number
  updated: number
  skipped: number
  status: string
}

/* ------------------------------------------------------------------- Drive */

export type DriveAuthState = 'credentials_missing' | 'needs_consent' | 'authorized'

export interface DriveStatusResponse {
  /** True only when a valid (or refreshable) token exists. */
  configured: boolean
  message: string
  state: DriveAuthState
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
