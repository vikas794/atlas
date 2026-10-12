import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, request } from '../lib/api'

function mockFetchOnce(response: Response) {
  const fetchMock = vi.fn().mockResolvedValue(response)
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('ApiError', () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('parses the { code, message, details } envelope from the backend', async () => {
    mockFetchOnce(
      jsonResponse(
        {
          code: 'drive_not_configured',
          message: 'No Google Drive credentials found.',
          details: { path: '/app/credentials.json' },
        },
        404,
      ),
    )

    const error = await request('/api/drive/status').catch((cause: unknown) => cause)

    expect(error).toBeInstanceOf(ApiError)
    const apiError = error as ApiError
    expect(apiError.message).toBe('No Google Drive credentials found.')
    expect(apiError.code).toBe('drive_not_configured')
    expect(apiError.details).toEqual({ path: '/app/credentials.json' })
    expect(apiError.status).toBe(404)
  })

  it('falls back cleanly when the error response is not JSON', async () => {
    mockFetchOnce(
      new Response('<html><body>502 Bad Gateway</body></html>', {
        status: 502,
        statusText: 'Bad Gateway',
      }),
    )

    const error = await request('/api/usage').catch((cause: unknown) => cause)

    expect(error).toBeInstanceOf(ApiError)
    const apiError = error as ApiError
    expect(apiError.message).toBe('Bad Gateway')
    expect(apiError.code).toBe('server_error')
    expect(apiError.details).toEqual({})
    expect(apiError.status).toBe(502)
  })

  it('falls back to a derived code when the body has no code field', async () => {
    mockFetchOnce(jsonResponse({ detail: 'Not Found' }, 404))

    const error = (await request('/api/playlists').catch((cause: unknown) => cause)) as ApiError

    expect(error.code).toBe('not_found')
    expect(error.message).toBe('Not Found')
  })
})
