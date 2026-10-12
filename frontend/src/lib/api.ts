import type {
  ActionResponse,
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

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

const DEFAULT_ERROR_MESSAGE = 'Request failed.'
const NETWORK_ERROR_MESSAGE = 'Could not reach the Atlas backend.'

/**
 * Every failed call throws this, so a screen can show `message` to the user
 * and branch on `code` instead of guessing from the HTTP status.
 */
export class ApiError extends Error {
  code: string
  details: unknown
  status: number

  constructor(message: string, code: string, details: unknown, status: number) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.details = details
    this.status = status
  }
}

/** Fallback codes for responses that do not carry the backend envelope. */
function codeForStatus(status: number): string {
  if (status === 400 || status === 422) return 'validation_error'
  if (status === 401 || status === 403) return 'unauthorized'
  if (status === 404) return 'not_found'
  if (status === 409) return 'conflict'
  if (status === 413) return 'payload_too_large'
  if (status >= 500) return 'server_error'
  return 'request_failed'
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

/**
 * Turn a non-OK response into an `ApiError`. The backend envelope is
 * `{ code, message, details }`.
 */
async function toApiError(response: Response): Promise<ApiError> {
  const fallback = response.statusText || DEFAULT_ERROR_MESSAGE
  let body: unknown

  try {
    const raw = await response.text()
    if (raw) body = JSON.parse(raw) as unknown
  } catch {
    body = undefined
  }

  if (!isRecord(body)) {
    return new ApiError(fallback, codeForStatus(response.status), {}, response.status)
  }

  const message =
    typeof body.message === 'string' && body.message.trim()
      ? body.message
      : typeof body.detail === 'string' && body.detail.trim()
        ? body.detail
        : fallback

  const code =
    typeof body.code === 'string' && body.code.trim() ? body.code : codeForStatus(response.status)

  return new ApiError(message, code, body.details ?? {}, response.status)
}

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
    throw new ApiError(NETWORK_ERROR_MESSAGE, 'network_error', { cause: String(cause) }, 0)
  }

  if (!response.ok) {
    throw await toApiError(response)
  }

  if (response.status === 204) return undefined as T

  const text = await response.text()
  if (!text) return undefined as T

  return JSON.parse(text) as T
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
  // The backend 500s on unknown stages, so never forward anything else.
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
 * Sync backends answer 200 with the new summary; async ones answer 202 with an
 * action envelope and the notes arrive later (poll the video stage).
 */
export type RegenerateResponse = Summary | ActionResponse

export function isSummaryResponse(value: RegenerateResponse | undefined): value is Summary {
  return Boolean(value) && typeof (value as Summary).content === 'string'
}

export function regenerateVideo(videoId: number) {
  return request<RegenerateResponse>(`/api/videos/${videoId}/regenerate`, {
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
