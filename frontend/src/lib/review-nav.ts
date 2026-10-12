import type { Stage } from './types'

/**
 * Id of the next video that needs review: the first one after `currentId` in
 * playlist order, wrapping to the first one before it. `null` when no other
 * video is waiting. Works on the full video list, so it is correct for
 * playlists of any size.
 */
export function findNextNeedingReview(
  videos: ReadonlyArray<{ id: number; stage: Stage }>,
  currentId: number,
): number | null {
  const index = videos.findIndex((video) => video.id === currentId)
  const after = videos.slice(index + 1).find((video) => video.stage === 'pending_review')
  if (after) return after.id
  const before = index < 0 ? undefined : videos.slice(0, index).find((video) => video.stage === 'pending_review')
  return before ? before.id : null
}
