import { expect, test, type Page, type Route } from '@playwright/test'

const STAMP = '2026-10-10T12:00:00Z'
const PLAYLIST_URL = 'https://www.youtube.com/playlist?list=PLsmoke123'

function playlist(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    playlist_id: 'PLsmoke123',
    source_url: PLAYLIST_URL,
    title: 'Smoke Test Course',
    video_count: 2,
    status: 'ingested',
    drive_folder_id: null,
    drive_index_doc_id: null,
    created_at: STAMP,
    updated_at: STAMP,
    last_synced_at: STAMP,
    stage_counts: { pending_review: 2 },
    last_publish_error: null,
    ...overrides,
  }
}

function video(id: number, overrides: Record<string, unknown> = {}) {
  return {
    id,
    playlist_id: 1,
    youtube_video_id: `yt${id}`,
    title: `Lesson ${id}`,
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
    auto_retry: false,
    retries_exhausted: false,
    duplicate_of: null,
    regenerating: false,
    max_auto_retries: 5,
    edited: false,
    ...overrides,
  }
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

/** A tiny in-memory backend that follows the real API contract. */
async function mockBackend(page: Page) {
  const state = { added: false, approved: new Set<number>() }
  const videos = () =>
    [1, 2].map((id) => video(id, state.approved.has(id) ? { stage: 'approved' } : {}))

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const { pathname } = new URL(request.url())
    const method = request.method()

    if (method === 'GET' && pathname === '/api/playlists') {
      const items = state.added ? [playlist()] : []
      return json(route, { playlists: items, total: items.length })
    }
    if (method === 'POST' && pathname === '/api/playlists') {
      state.added = true
      return json(route, playlist(), 201)
    }
    if (method === 'GET' && pathname === '/api/playlists/1') {
      const approved = state.approved.size
      return json(route, playlist({ stage_counts: { pending_review: 2 - approved, approved } }))
    }
    if (method === 'GET' && pathname === '/api/playlists/1/videos') {
      const list = videos()
      return json(route, { videos: list, total: list.length, page: 1, page_size: 50 })
    }
    const summary = pathname.match(/^\/api\/videos\/(\d+)\/summary$/)
    if (method === 'GET' && summary) {
      const id = Number(summary[1])
      return json(route, {
        id,
        video_id: id,
        content: `# Lesson ${id}\n\nGenerated **notes** for lesson ${id}.`,
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
        needs_republish: false,
      })
    }
    const approve = pathname.match(/^\/api\/videos\/(\d+)\/approve$/)
    if (method === 'POST' && approve) {
      const id = Number(approve[1])
      state.approved.add(id)
      return json(route, video(id, { stage: 'approved' }))
    }
    return json(
      route,
      { code: 'NOT_FOUND', message: `No mock for ${method} ${pathname}`, details: {} },
      404,
    )
  })
}

test('add a playlist, see its videos, open the review and approve', async ({ page }) => {
  await mockBackend(page)
  await page.goto('/')

  await expect(page.getByText('No playlists yet.')).toBeVisible()
  await page.getByRole('textbox', { name: 'Paste a YouTube playlist link' }).fill(PLAYLIST_URL)
  await page.getByRole('button', { name: 'Add playlist' }).click()
  await expect(page.getByText('Added "Smoke Test Course" with 2 videos.')).toBeVisible()

  await page.getByRole('link', { name: 'Smoke Test Course' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Smoke Test Course' })).toBeVisible()
  await expect(page.getByRole('row', { name: /001 Lesson 1/ })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Approve all (2)' })).toBeEnabled()

  await page.getByRole('link', { name: 'Lesson 1' }).first().click()
  await expect(page.getByRole('heading', { level: 1, name: '#001 Lesson 1' })).toBeVisible()
  await expect(page.getByText('Generated notes for lesson 1.')).toBeVisible()

  await page.getByRole('button', { name: 'Approve', exact: true }).click()
  await expect(page.getByText('Video approved.')).toBeVisible()
  // Approve-and-next moves on to the next video that needs review.
  await expect(page.getByRole('heading', { level: 1, name: '#002 Lesson 2' })).toBeVisible()
})

test('shows the backend error message when adding a playlist fails (502 envelope)', async ({
  page,
}) => {
  await page.route('**/api/playlists', (route) => {
    if (route.request().method() === 'GET') return json(route, { playlists: [], total: 0 })
    return json(
      route,
      {
        code: 'PROVIDER_ERROR_YOUTUBE',
        message: 'YouTube did not respond. Try again in a minute.',
        details: {},
      },
      502,
    )
  })
  await page.goto('/')

  await page.getByRole('textbox', { name: 'Paste a YouTube playlist link' }).fill(PLAYLIST_URL)
  await page.getByRole('button', { name: 'Add playlist' }).click()

  await expect(page.locator('#playlist-url-error')).toHaveText(
    'YouTube did not respond. Try again in a minute.',
  )
  await expect(
    page.getByText('Could not add the playlist: YouTube did not respond. Try again in a minute.'),
  ).toBeVisible()
})

test('shows a readable message when the backend is down (500, non-JSON)', async ({ page }) => {
  await page.route('**/api/**', (route) =>
    route.fulfill({ status: 500, contentType: 'text/plain', body: 'Internal Server Error' }),
  )
  await page.goto('/')

  await expect(page.getByText('Could not load playlists')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText(/failed to handle the request \(HTTP 500\)/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible()
})
