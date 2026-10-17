import type { QueryClient } from '@tanstack/react-query'

/** One place for every TanStack query key, so invalidation never misses a cache entry. */
export const queryKeys = {
  playlists: ['playlists'] as const,
  playlist: (id: number) => ['playlist', id] as const,
  /** Prefix of every video-list query of a playlist (pages and the full nav list). */
  videos: (id: number) => ['playlist-videos', id] as const,
  videosPage: (id: number, filter: string, search: string, page: number) =>
    ['playlist-videos', id, 'page', filter, search, page] as const,
  videosAll: (id: number) => ['playlist-videos', id, 'all'] as const,
  summary: (videoId: number) => ['video-summary', videoId] as const,
  diagnostics: ['diagnostics'] as const,
  settings: ['settings'] as const,
  models: (provider: string) => ['settings', 'models', provider] as const,
}

/** After anything that changes a playlist or its videos (process, reprocess, publish, resync...). */
export function invalidatePlaylist(queryClient: QueryClient, playlistId: number): Promise<unknown> {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: queryKeys.playlists }),
    queryClient.invalidateQueries({ queryKey: queryKeys.playlist(playlistId) }),
    queryClient.invalidateQueries({ queryKey: queryKeys.videos(playlistId) }),
  ])
}

/** After anything that changes one video (approve, retry, regenerate, clear transcript, edit). */
export function invalidateVideo(
  queryClient: QueryClient,
  playlistId: number,
  videoId: number,
): Promise<unknown> {
  return Promise.all([
    invalidatePlaylist(queryClient, playlistId),
    queryClient.invalidateQueries({ queryKey: queryKeys.summary(videoId) }),
  ])
}
