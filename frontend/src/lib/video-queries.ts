import { getPlaylistVideos } from './api'
import { STAGE_FILTERS, type StageFilter } from './progress'
import type { Video, VideoListResponse } from './types'

/** Backend cap for `page_size` (see `list_playlist_videos`). */
export const MAX_PAGE_SIZE = 500

/**
 * Videos for a summary-strip filter.
 *
 * The backend accepts one `stage` per request (and 500s on unknown values), so:
 * - `all` and single-stage filters map straight onto one paged request;
 * - the multi-stage "In progress" group issues one request per stage, merges
 *   the results by position and paginates on the client. That set is bounded
 *   by the worker concurrency, so the extra requests stay cheap and the table
 *   shows exactly the videos the card counted.
 */
export async function fetchPlaylistVideos(
  playlistId: number,
  options: { filter: StageFilter; search?: string; page: number; pageSize: number },
): Promise<VideoListResponse> {
  const { filter, search, page, pageSize } = options
  const stages = STAGE_FILTERS[filter]

  if (!stages || stages.length === 1) {
    return getPlaylistVideos(playlistId, {
      stage: stages?.[0],
      search: search || undefined,
      page,
      page_size: pageSize,
    })
  }

  const responses = await Promise.all(
    stages.map((stage) =>
      getPlaylistVideos(playlistId, { stage, search: search || undefined, page: 1, page_size: MAX_PAGE_SIZE }),
    ),
  )
  const merged: Video[] = responses
    .flatMap((response) => response.videos)
    .sort((a, b) => a.position - b.position || a.id - b.id)

  const start = (page - 1) * pageSize
  return {
    videos: merged.slice(start, start + pageSize),
    total: merged.length,
    page,
    page_size: pageSize,
  }
}

/**
 * Every video of a playlist (for the review rail and "next needing review").
 * Pages through the API until it is exhausted, so playlists over 250 (or 500)
 * videos still resolve every video id.
 */
export async function fetchAllPlaylistVideos(playlistId: number): Promise<Video[]> {
  const first = await getPlaylistVideos(playlistId, { page: 1, page_size: MAX_PAGE_SIZE })
  const videos = [...first.videos]
  let page = 1
  while (videos.length < first.total && page < 100) {
    page += 1
    const next = await getPlaylistVideos(playlistId, { page, page_size: MAX_PAGE_SIZE })
    if (next.videos.length === 0) break
    videos.push(...next.videos)
  }
  return videos
}
