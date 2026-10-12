import { formatCountdown } from './format'
import type { FailureKind, FailureType, Stage, Video } from './types'

import type { BadgeTone } from '../components/ui/badge'

/**
 * The single source of truth for stage -> badge mapping (design.md section 9).
 *
 * Every surface that shows a status — the videos table, the summary strip and
 * the review screen — goes through here, so they cannot disagree. Never
 * hardcode a stage label or colour anywhere else.
 */

export interface BadgeSpec {
  /** Human label. design.md requires sentence case. */
  label: string
  /** Semantic colour token group. */
  tone: BadgeTone
  /** Lucide icon name; status is never conveyed by colour alone (section 8). */
  icon: string
  /** True when the stage represents work still in flight. */
  inProgress: boolean
  /** Extra tag shown beside the stage (e.g. "No longer in playlist"). */
  secondary?: BadgeSpec
}

/** A fallback reason shown when the backend sends a stage we do not recognise. */
const UNKNOWN_BADGE: BadgeSpec = {
  label: 'Unknown',
  tone: 'neutral',
  icon: 'help-circle',
  inProgress: false,
}

const BY_STAGE: Record<Stage, BadgeSpec> = {
  queued: { label: 'Queued', tone: 'neutral', icon: 'clock', inProgress: false },
  fetching_transcript: {
    label: 'Fetching transcript',
    tone: 'progress',
    icon: 'loader',
    inProgress: true,
  },
  transcript_ready: {
    label: 'Transcript ready',
    tone: 'neutral',
    icon: 'file-text',
    inProgress: false,
  },
  generating_notes: {
    label: 'Generating notes',
    tone: 'progress',
    icon: 'loader',
    inProgress: true,
  },
  pending_review: { label: 'Needs review', tone: 'review', icon: 'eye', inProgress: false },
  approved: { label: 'Approved', tone: 'success', icon: 'check', inProgress: false },
  published: { label: 'Published', tone: 'success', icon: 'cloud-check', inProgress: false },
  // `failed` is resolved by kind in stageToBadge; this entry is the safety net.
  failed: { label: 'Failed', tone: 'error', icon: 'x-circle', inProgress: false },
}

const NO_LONGER_IN_PLAYLIST: BadgeSpec = {
  label: 'No longer in playlist',
  tone: 'neutral',
  icon: 'archive',
  inProgress: false,
}

/** Transient failure whose automatic retry is still scheduled. */
const RETRYING: BadgeSpec = { label: 'Retrying', tone: 'warn', icon: 'refresh', inProgress: true }

/** Transient failure whose automatic retries are over; only manual Retry is left. */
const FAILED_TEMPORARY: BadgeSpec = {
  label: 'Failed – temporary',
  tone: 'warn',
  icon: 'refresh',
  inProgress: false,
}

const FAILED_PERMANENT: BadgeSpec = {
  label: 'Failed',
  tone: 'error',
  icon: 'x-circle',
  inProgress: false,
}

/** The fields the retry logic reads; any `Video` satisfies this. */
export type RetryFields = Pick<Video, 'stage' | 'failure_kind' | 'retry_count'> &
  Partial<Pick<Video, 'next_retry_at' | 'auto_retry' | 'retries_exhausted' | 'max_auto_retries'>>

/**
 * True only when an automatic retry is actually scheduled: the backend says so
 * (`auto_retry`) or a `next_retry_at` is set. A transient failure that has run
 * out of retries is NOT "retrying" (design.md 3.3: "Failed – temporary").
 */
export function isRetryScheduled(video: RetryFields): boolean {
  if (video.stage !== 'failed' || video.failure_kind !== 'transient') return false
  if (video.retries_exhausted === true || video.auto_retry === false) return false
  return video.auto_retry === true || Boolean(video.next_retry_at)
}

/** A transient failure with nothing scheduled: only the manual Retry button is left. */
export function isRetriesExhausted(video: RetryFields): boolean {
  return video.stage === 'failed' && video.failure_kind === 'transient' && !isRetryScheduled(video)
}

/**
 * Map a stage (plus failure kind and retry state) to a badge.
 *
 * `removedUpstream` never hides the real stage (design.md 3.3 lists it as a
 * flag): the real badge is returned with a `secondary` "No longer in playlist".
 */
export function stageToBadge(
  stage: Stage,
  failureKind?: FailureKind | null,
  options: { removedUpstream?: boolean; retryScheduled?: boolean } = {},
): BadgeSpec {
  let spec: BadgeSpec

  if (stage === 'failed') {
    if (failureKind === 'transient') {
      spec = options.retryScheduled ? RETRYING : FAILED_TEMPORARY
    } else {
      spec = FAILED_PERMANENT
    }
  } else {
    spec = BY_STAGE[stage] ?? UNKNOWN_BADGE
  }

  return options.removedUpstream ? { ...spec, secondary: NO_LONGER_IN_PLAYLIST } : spec
}

/** Badge for a whole video: the one place retry state is folded in. */
export function videoBadge(video: RetryFields & { removed_upstream?: boolean }): BadgeSpec {
  return stageToBadge(video.stage, video.failure_kind, {
    removedUpstream: video.removed_upstream,
    retryScheduled: isRetryScheduled(video),
  })
}

/** Human sentence for a failure reason, shown in the table's Detail column. */
export function failureLabel(
  failureType: FailureType | null | undefined,
  failureReason: string | null | undefined,
): string {
  return failureReason?.trim() || defaultReasonFor(failureType) || 'Failed'
}

/**
 * Fallback copy when the backend sent a failure_type but no reason.
 * Written as plain sentences because design.md section 6.2 shows these verbatim
 * in the Detail column — never surface a snake_case code to the user.
 */
function defaultReasonFor(failureType: FailureType | null | undefined): string {
  switch (failureType) {
    case 'video_private':
      return 'Video is private'
    case 'video_deleted':
      return 'Video was deleted by the uploader'
    case 'age_restricted':
      return 'Video is age-restricted'
    case 'region_blocked':
      return 'Video is not available in this region'
    case 'members_only':
      return 'Members-only video'
    case 'transcripts_disabled':
      return 'Transcripts are disabled by the uploader'
    case 'no_usable_language':
      return 'No transcript available in a usable language'
    case 'transcript_too_large':
      return 'Transcript is too long for the model input limit'
    case 'rate_limited':
      return 'Rate limited by the provider'
    case 'network_error':
      return 'Temporary network error'
    case 'provider_error':
      return 'The provider returned an error'
    case 'output_malformed':
      return 'The model returned an unusable response'
    case 'playlist_unavailable':
      return 'Playlist is unavailable'
    case 'invalid_url':
      return 'Invalid playlist URL'
    case 'drive_error':
      return 'Google Drive error'
    default:
      return ''
  }
}


export interface VideoDetailSpec {
  /** Plain-text failure reason, or null when there is nothing to explain. */
  reason: string | null
  /** Retry status line: "Retrying in 2m (attempt 3/5)" or "Retries exhausted - use Retry". */
  note: string | null
  /** Transient reasons are warn-coloured, permanent ones error-coloured (design 6.2). */
  tone: 'error' | 'warn' | 'muted'
  /** Show the "edited" tag. */
  edited: boolean
}

/** "attempt 3/5", or "attempt 3" when the backend does not expose the cap. */
export function attemptLabel(retryCount: number, maxRetries?: number | null): string {
  const attempt = Math.max(retryCount, 1)
  return typeof maxRetries === 'number' && maxRetries > 0
    ? `attempt ${attempt}/${maxRetries}`
    : `attempt ${attempt}`
}

/**
 * Detail-column content for a video row (design.md 6.2): the failure reason in
 * plain text, the retry countdown or exhaustion note, and the "edited" tag.
 */
export function videoDetail(
  video: RetryFields & Partial<Pick<Video, 'failure_type' | 'failure_reason' | 'edited'>>,
  now: number = Date.now(),
): VideoDetailSpec {
  const edited = video.edited === true

  if (video.stage !== 'failed') {
    return { reason: null, note: null, tone: 'muted', edited }
  }

  const reason = failureLabel(video.failure_type, video.failure_reason)

  if (video.failure_kind !== 'transient') {
    return { reason, note: null, tone: 'error', edited }
  }

  if (isRetryScheduled(video)) {
    const countdown = video.next_retry_at ? formatCountdown(video.next_retry_at, now) : ''
    const when = !countdown ? 'Retrying' : countdown === 'now' ? 'Retrying now' : `Retrying in ${countdown}`
    return {
      reason,
      note: `${when} (${attemptLabel(video.retry_count, video.max_auto_retries)})`,
      tone: 'warn',
      edited,
    }
  }

  return { reason, note: 'Retries exhausted - use Retry', tone: 'warn', edited }
}
