import type { Playlist, Video } from './types'
import { ACTIVE_STAGES } from './types'

/**
 * Publish and regenerate may be synchronous (200, old backend) or asynchronous
 * (202, new backend). For the async case the UI "watches" the resource it
 * polls until the job is done. `startedAt` is when the request returned; data
 * fetched before that moment is stale and never counts as "finished".
 */
export interface JobWatch {
  startedAt: number
  /** Published-video count when the job started, to report a real delta. */
  publishedBefore?: number
}

export type JobPhase = 'idle' | 'running' | 'succeeded' | 'failed'

export function publishPhase(
  playlist: Pick<Playlist, 'status'> | undefined,
  dataUpdatedAt: number,
  watch: JobWatch | null,
): JobPhase {
  if (!watch) return 'idle'
  if (!playlist || dataUpdatedAt < watch.startedAt) return 'running'
  if (playlist.status === 'publishing') return 'running'
  if (playlist.status === 'publish_failed') return 'failed'
  return 'succeeded'
}

export function regeneratePhase(
  video: Pick<Video, 'stage'> | undefined,
  dataUpdatedAt: number,
  watch: JobWatch | null,
): JobPhase {
  if (!watch) return 'idle'
  if (!video || dataUpdatedAt < watch.startedAt) return 'running'
  if (video.stage === 'queued' || ACTIVE_STAGES.includes(video.stage)) return 'running'
  if (video.stage === 'failed') return 'failed'
  return 'succeeded'
}

export interface PublishCounts {
  created: number
  updated: number
  skipped?: number
}

/** True when the response of POST /publish already carries counts (sync, 200). */
export function hasPublishCounts(
  response: Partial<PublishCounts> | undefined,
): response is PublishCounts {
  return typeof response?.created === 'number' && typeof response?.updated === 'number'
}

/** Toast text for a finished publish, with real counts and no "0 new Docs!". */
export function describePublish(counts: PublishCounts | null, publishedDelta = 0): string {
  if (counts) {
    const parts: string[] = []
    if (counts.created > 0)
      parts.push(`Published ${counts.created} new ${counts.created === 1 ? 'Doc' : 'Docs'}`)
    if (counts.updated > 0) {
      parts.push(
        `${parts.length ? 'updated' : 'Updated'} ${counts.updated} ${counts.updated === 1 ? 'Doc' : 'Docs'}`,
      )
    }
    return parts.length ? `${parts.join(', ')} in Drive.` : 'Nothing new to publish.'
  }
  if (publishedDelta > 0) {
    return `Published ${publishedDelta} ${publishedDelta === 1 ? 'video' : 'videos'} to Drive.`
  }
  return 'Nothing new to publish.'
}
