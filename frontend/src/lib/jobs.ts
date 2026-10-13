import type { Playlist, Video } from './types'
import { ACTIVE_STAGES } from './types'

/**
 * Publish and regenerate run in the backend worker (HTTP 202), so the UI
 * "watches" the resource it polls until the job is done. `startedAt` is when
 * the request returned; data fetched before that moment is stale and never
 * counts as "finished".
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

/** Toast text for a finished publish: how many more videos are now published. */
export function describePublish(publishedDelta: number): string {
  if (publishedDelta > 0) {
    return `Published ${publishedDelta} ${publishedDelta === 1 ? 'video' : 'videos'} to Drive.`
  }
  return 'Publish finished. Nothing new to publish.'
}
