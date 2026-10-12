import { isRetryScheduled, type RetryFields } from './stage-badge'
import { ACTIVE_STAGES, type Playlist, type Stage } from './types'

/** design.md 6.2: poll fast while anything moves, slowly otherwise. */
export const FAST_POLL_MS = 3000
export const SLOW_POLL_MS = 15000

/** Stages that mean "work is waiting or running" (active work plus queued). */
const BUSY_STAGES: readonly Stage[] = ['queued', ...ACTIVE_STAGES]

/** True while any video is queued/running or has an automatic retry pending. */
export function videosAreBusy(videos: ReadonlyArray<RetryFields> | undefined): boolean {
  return (videos ?? []).some((video) => BUSY_STAGES.includes(video.stage) || isRetryScheduled(video))
}

/** True when the counts show queued or active videos, or a publish is running. */
export function playlistIsBusy(
  playlist: Pick<Playlist, 'status' | 'stage_counts'> | undefined,
): boolean {
  if (!playlist) return false
  if (playlist.status === 'publishing') return true
  return BUSY_STAGES.some((stage) => (playlist.stage_counts?.[stage] ?? 0) > 0)
}

/**
 * Pure poll-interval selector used by both the playlist and videos queries.
 * 3s when any video is queued, fetching_transcript, transcript_ready or
 * generating_notes, a transient retry is pending, the playlist is publishing
 * or an async regenerate/publish is running; otherwise 15s. (TanStack pauses
 * the interval in hidden tabs by default.)
 */
export function pollInterval(input: {
  videos?: ReadonlyArray<RetryFields>
  playlist?: Pick<Playlist, 'status' | 'stage_counts'>
  asyncRunning?: boolean
}): number {
  const busy =
    input.asyncRunning === true || playlistIsBusy(input.playlist) || videosAreBusy(input.videos)
  return busy ? FAST_POLL_MS : SLOW_POLL_MS
}
