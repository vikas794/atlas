import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { vi } from 'vitest'

import { routes } from '../routes'
import type { Playlist, Summary, Video } from '../lib/types'

// The routes load their screens lazily. Importing the screens here moves their
// (slow, cold) transform into test collection, so the first `findBy*` of a file
// does not time out while Vite is still compiling the screen on a busy machine.
import '../features/playlists/playlists-home'
import '../features/playlists/playlist-detail'
import '../features/playlists/review-screen'
import '../features/settings/settings-screen'

/* ------------------------------------------------------------------ fetch mock */

export interface RecordedCall {
  method: string
  path: string
  search: URLSearchParams
  body: unknown
}

export type Responder = (call: RecordedCall) => { status?: number; body?: unknown } | unknown

export interface MockApi {
  calls: RecordedCall[]
  fetchMock: ReturnType<typeof vi.fn>
  /** Register a route; a function responder is called with the request, anything else is the JSON body. */
  on: {
    (method: string, match: string | RegExp, respond: Responder): MockApi
    (method: string, match: string | RegExp, body: object): MockApi
  }
  callsTo(method: string, match: string | RegExp): RecordedCall[]
}

interface Route {
  method: string
  match: string | RegExp
  respond: Responder
}

/** Build a JSON response; a `{ status, body }` envelope sets the HTTP status. */
function toResponse(result: unknown): Response {
  if (
    result &&
    typeof result === 'object' &&
    'status' in result &&
    typeof (result as { status: unknown }).status === 'number'
  ) {
    const { status, body } = result as { status: number; body?: unknown }
    return new Response(body === undefined ? null : JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    })
  }
  return new Response(JSON.stringify(result), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

/**
 * Stub `fetch` with route handlers. Routes are matched in registration order by
 * HTTP method and path (string equality or RegExp). Every call is recorded.
 */
export function mockApi() {
  const table: Route[] = []
  const calls: RecordedCall[] = []

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://localhost')
    const method = (init?.method ?? 'GET').toUpperCase()
    let body: unknown
    if (typeof init?.body === 'string') {
      try {
        body = JSON.parse(init.body)
      } catch {
        body = init.body
      }
    }
    const call: RecordedCall = { method, path: url.pathname, search: url.searchParams, body }
    calls.push(call)

    const route = table.find(
      (candidate) =>
        candidate.method === method &&
        (typeof candidate.match === 'string'
          ? candidate.match === url.pathname
          : candidate.match.test(url.pathname)),
    )
    if (!route) {
      return new Response(JSON.stringify({ code: 'not_found', message: `No mock for ${method} ${url.pathname}` }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    return toResponse(await route.respond(call))
  })
  vi.stubGlobal('fetch', fetchMock)

  const api: MockApi = {
    calls,
    fetchMock,
    on: ((method: string, match: string | RegExp, respond: Responder | object) => {
      // Later registrations win, so a test can override a default route.
      table.unshift({
        method: method.toUpperCase(),
        match,
        respond: typeof respond === 'function' ? (respond as Responder) : () => respond,
      })
      return api
    }) as MockApi['on'],
    /** Recorded calls to a method + path. */
    callsTo(method: string, match: string | RegExp) {
      return calls.filter(
        (call) =>
          call.method === method.toUpperCase() &&
          (typeof match === 'string' ? call.path === match : match.test(call.path)),
      )
    },
  }
  return api
}

/* ------------------------------------------------------------------ fixtures */

const STAMP = '2026-10-10T12:00:00Z'

export function makePlaylist(overrides: Partial<Playlist> = {}): Playlist {
  return {
    id: 1,
    playlist_id: 'PL1',
    source_url: 'https://www.youtube.com/playlist?list=PL1',
    title: 'Complete Java',
    video_count: 3,
    status: 'ready',
    drive_folder_id: null,
    drive_index_doc_id: null,
    created_at: STAMP,
    updated_at: STAMP,
    last_synced_at: STAMP,
    stage_counts: {
      queued: 0,
      fetching_transcript: 0,
      transcript_ready: 0,
      generating_notes: 0,
      pending_review: 0,
      approved: 0,
      published: 0,
      failed: 0,
    },
    ...overrides,
  }
}

export function makeVideo(id: number, overrides: Partial<Video> = {}): Video {
  return {
    id,
    playlist_id: 1,
    youtube_video_id: `yt${id}`,
    title: `Video ${id}`,
    position: id,
    stage: 'pending_review',
    failure_type: null,
    failure_kind: null,
    failure_reason: null,
    retry_count: 0,
    next_retry_at: null,
    drive_doc_id: null,
    removed_upstream: false,
    created_at: STAMP,
    updated_at: STAMP,
    ...overrides,
  }
}

export function makeSummary(videoId: number, overrides: Partial<Summary> = {}): Summary {
  return {
    id: videoId,
    video_id: videoId,
    content: `# Notes ${videoId}`,
    provider: 'gemini',
    model: 'gemini-test',
    prompt_version: 'v1',
    generated_at: STAMP,
    edited: false,
    approved: false,
    approved_at: null,
    published: false,
    published_at: null,
    published_digest: null,
    ...overrides,
  }
}

export function videoList(videos: Video[], total = videos.length) {
  return { videos, total, page: 1, page_size: 50 }
}

/* ------------------------------------------------------------------ render */

export function renderRoute(path: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } },
  })
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { ...utils, router, queryClient }
}
