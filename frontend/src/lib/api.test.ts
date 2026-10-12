import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, request } from '../lib/api'
import { errorMessage, failureMessage } from '../lib/errors'

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
    expect(apiError.message).toBe(
      'The Atlas backend failed to handle the request (HTTP 502). Try again; if it keeps failing, check Diagnostics.',
    )
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

  it('turns a 500 with a non-JSON body into a readable message', async () => {
    mockFetchOnce(new Response('Internal Server Error', { status: 500 }))

    const error = (await request('/api/playlists').catch((cause: unknown) => cause)) as ApiError

    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(500)
    expect(error.message).toMatch(/failed to handle the request \(HTTP 500\)/)
    expect(error.message).not.toContain('Internal Server Error')
  })

  it('reports a network failure (backend down) as a readable ApiError', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    const error = (await request('/api/playlists').catch((cause: unknown) => cause)) as ApiError

    expect(error).toBeInstanceOf(ApiError)
    expect(error.code).toBe('network_error')
    expect(error.status).toBe(0)
    expect(error.message).toBe(
      'Could not reach the Atlas backend. Check that it is running, then try again.',
    )
  })

  it('rejects a 200 whose body is not JSON instead of throwing a SyntaxError', async () => {
    mockFetchOnce(new Response('<!doctype html><html></html>', { status: 200 }))

    const error = (await request('/api/playlists').catch((cause: unknown) => cause)) as ApiError

    expect(error).toBeInstanceOf(ApiError)
    expect(error.code).toBe('invalid_response')
    expect(error.message).toMatch(/could not be read/)
  })

  it('names the first validation problem of a 422', async () => {
    mockFetchOnce(
      jsonResponse(
        {
          code: 'VALIDATION_ERROR',
          message: 'Request validation failed',
          details: { errors: [{ loc: ['body', 'url'], msg: 'Field required', type: 'missing' }] },
        },
        422,
      ),
    )

    const error = (await request('/api/playlists').catch((cause: unknown) => cause)) as ApiError

    expect(error.code).toBe('VALIDATION_ERROR')
    expect(error.message).toBe('Request validation failed: Field required (url)')
  })

  it('adds the next step for Drive authorization errors', async () => {
    mockFetchOnce(
      jsonResponse(
        { code: 'DRIVE_AUTH_REQUIRED', message: 'Google Drive is not authorized', details: {} },
        409,
      ),
    )

    const error = (await request('/api/playlists/1/publish').catch(
      (cause: unknown) => cause,
    )) as ApiError

    expect(error.message).toBe(
      'Google Drive is not authorized. Connect Google Drive in Settings, then try again.',
    )
  })

  it('falls back to the status sentence when the envelope message is empty', async () => {
    mockFetchOnce(jsonResponse({ code: 'HTTP_404', message: '', details: { reason: 'x' } }, 404))

    const error = (await request('/api/nope').catch((cause: unknown) => cause)) as ApiError

    expect(error.message).toBe('The requested item was not found. It may have been deleted.')
  })
})

describe('errorMessage / failureMessage', () => {
  it('prefixes the action and never returns an empty reason', () => {
    expect(failureMessage('Could not delete the playlist', new Error('Playlist 3 not found'))).toBe(
      'Could not delete the playlist: Playlist 3 not found',
    )
    expect(errorMessage(new Error('   '), 'fallback')).toBe('fallback')
    expect(errorMessage(undefined)).toBe('Something went wrong.')
    expect(errorMessage('plain string')).toBe('plain string')
  })
})
