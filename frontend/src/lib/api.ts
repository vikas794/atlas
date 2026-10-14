import type {
  ActionResponse,
  DiagnosticsResponse,
  DriveActionResponse,
  DriveStatusResponse,
  Playlist,
  PlaylistListResponse,
  PublishResponse,
  Stage,
  Summary,
  UsageAggregateResponse,
  Video,
  VideoListResponse,
} from './types'
import { ALL_STAGES } from './types'
import { ApiError, invalidResponseError, networkError, toApiError } from './errors'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

// Errors live in errors.ts (one handler for every call); re-exported for callers.
export { ApiError }

function defaultHeaders(init?: RequestInit): HeadersInit {
  const supplied = init?.headers ?? {}
  const isMultipart = typeof FormData !== 'undefined' && init?.body instanceof FormData
  if (isMultipart) return supplied
  return { 'Content-Type': 'application/json', ...supplied }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response

  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: defaultHeaders(init),
    })
  } catch (cause) {
    throw networkError(cause)
  }

  if (!response.ok) {
    throw await toApiError(response)
  }

  if (response.status === 204) return undefined as T

  const text = await response.text()
  if (!text) return undefined as T

  try {
    return JSON.parse(text) as T
  } catch {
    // e.g. the dev server's index.html instead of the API (backend not proxied).
    throw invalidResponseError(response.status)
  }
}

/* --------------------------------------------------------------- Playlists */

export function getPlaylists(params?: { limit?: number; offset?: number }) {
  const query = new URLSearchParams()
  if (params?.limit) query.set('limit', String(params.limit))
  if (params?.offset) query.set('offset', String(params.offset))
  const qs = query.toString()
  return request<PlaylistListResponse>(`/api/playlists${qs ? `?${qs}` : ''}`)
}

export function getPlaylist(id: number) {
  return request<Playlist>(`/api/playlists/${id}`)
}

export function ingestPlaylist(url: string) {
  return request<Playlist>('/api/playlists', {
    method: 'POST',
    body: JSON.stringify({ url }),
  })
}

export function getPlaylistVideos(
  playlistId: number,
  params?: { stage?: Stage; search?: string; page?: number; page_size?: number },
) {
  const query = new URLSearchParams()
  // The backend rejects unknown stages with 422, so never forward anything else.
  if (params?.stage && ALL_STAGES.includes(params.stage)) query.set('stage', params.stage)
  if (params?.search) query.set('search', params.search)
  if (params?.page) query.set('page', String(params.page))
  if (params?.page_size) query.set('page_size', String(params.page_size))
  const qs = query.toString()
  return request<VideoListResponse>(`/api/playlists/${playlistId}/videos${qs ? `?${qs}` : ''}`)
}

export function processPlaylist(playlistId: number) {
  return request<ActionResponse>(`/api/playlists/${playlistId}/process`, {
    method: 'POST',
  })
}

export function reprocessPlaylist(playlistId: number) {
  return request<ActionResponse>(`/api/playlists/${playlistId}/reprocess`, {
    method: 'POST',
  })
}

export function deletePlaylist(playlistId: number) {
  return request<ActionResponse>(`/api/playlists/${playlistId}`, {
    method: 'DELETE',
  })
}

export function approveAllVideos(playlistId: number) {
  return request<ActionResponse>(`/api/playlists/${playlistId}/approve-all`, {
    method: 'POST',
  })
}

export function publishPlaylist(playlistId: number) {
  return request<PublishResponse>(`/api/playlists/${playlistId}/publish`, {
    method: 'POST',
  })
}

export function cleanupPlaylistTranscripts(playlistId: number) {
  return request<ActionResponse>(`/api/playlists/${playlistId}/transcripts`, {
    method: 'DELETE',
  })
}

/* ------------------------------------------------------------------ Videos */

export function getVideoSummary(videoId: number) {
  return request<Summary>(`/api/videos/${videoId}/summary`)
}

export function updateVideoSummary(videoId: number, content: string) {
  return request<Summary>(`/api/videos/${videoId}/summary`, {
    method: 'PUT',
    body: JSON.stringify({ content }),
  })
}

export function approveVideo(videoId: number) {
  return request<Video>(`/api/videos/${videoId}/approve`, {
    method: 'POST',
  })
}

export function retryVideo(videoId: number) {
  return request<Video>(`/api/videos/${videoId}/retry`, {
    method: 'POST',
  })
}

/**
 * Queue a regeneration (HTTP 202). The video goes back to `queued` with
 * `regenerating: true`; poll the video until it settles, then refetch notes.
 */
export function regenerateVideo(videoId: number) {
  return request<Video>(`/api/videos/${videoId}/regenerate`, {
    method: 'POST',
  })
}

export function deleteVideoTranscript(videoId: number) {
  return request<ActionResponse>(`/api/videos/${videoId}/transcript`, {
    method: 'DELETE',
  })
}

/* ------------------------------------------------------------------ Drive */

export function getDriveStatus() {
  return request<DriveStatusResponse>('/api/drive/status')
}

export function uploadDriveCredentials(file: File) {
  const formData = new FormData()
  formData.append('file', file)

  return request<DriveActionResponse>('/api/drive/credentials', {
    method: 'POST',
    body: formData,
  })
}

export function authenticateDrive() {
  return request<DriveActionResponse>('/api/drive/auth', {
    method: 'POST',
  })
}

/* ------------------------------------------------------------- Diagnostics */

/** Readiness checks plus the most recent backend errors (Diagnostics screen). */
export function getDiagnostics() {
  return request<DiagnosticsResponse>('/api/diagnostics')
}

/** Empties the backend's in-memory recent warnings/errors buffer. */
export function clearRecentErrors() {
  return request<void>('/api/diagnostics/recent-errors', { method: 'DELETE' })
}

/* ------------------------------------------------------------------ Usage */

export interface UsageQueryParams {
  provider?: string
  model?: string
  operation?: string
  since?: string
  until?: string
  cache_status?: boolean
}

export function getUsageAggregate(params?: UsageQueryParams) {
  const searchParams = new URLSearchParams()
  if (params?.provider) searchParams.set('provider', params.provider)
  if (params?.model) searchParams.set('model', params.model)
  if (params?.operation) searchParams.set('operation', params.operation)
  if (params?.since) searchParams.set('since', params.since)
  if (params?.until) searchParams.set('until', params.until)
  if (params?.cache_status !== undefined)
    searchParams.set('cache_status', String(params.cache_status))

  const query = searchParams.toString()
  return request<UsageAggregateResponse>(`/api/usage${query ? `?${query}` : ''}`)
}

export type { DriveActionResponse, DriveStatusResponse, UsageAggregateResponse }
