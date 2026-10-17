import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { Summary, Video } from '../../lib/types'
import {
  PUBLISH_ACCEPTED,
  makePlaylist,
  makeSummary,
  makeVideo,
  mockApi,
  renderRoute,
  videoList,
} from '../../test/harness'

function setup(options: { videos?: Video[]; summary?: Partial<Summary> } = {}) {
  const videos = options.videos ?? [makeVideo(1), makeVideo(2)]
  const api = mockApi()
  api.on('GET', '/api/playlists/1', makePlaylist())
  api.on('GET', '/api/playlists/1/videos', (call) => {
    const page = Number(call.search.get('page') ?? 1)
    const size = Number(call.search.get('page_size') ?? 50)
    return { ...videoList(videos.slice((page - 1) * size, page * size), videos.length), page }
  })
  api.on('GET', /\/api\/videos\/(\d+)\/summary/, (call) => {
    const id = Number(call.path.split('/')[3])
    return makeSummary(id, options.summary)
  })
  return api
}

async function openEditor(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole('heading', { level: 1, name: /#001 Video 1/ })
  await user.click(await screen.findByRole('tab', { name: 'Edit' }))
  return screen.findByRole('textbox', { name: /edit notes/i })
}

describe('ReviewScreen header', () => {
  it('shows the zero-padded position, metadata and an "Open video" link', async () => {
    setup()
    renderRoute('/playlists/1/review/1')

    expect(
      await screen.findByRole('heading', { level: 1, name: '#001 Video 1' }),
    ).toBeInTheDocument()
    expect(await screen.findByText(/Generated/)).toBeInTheDocument()
    expect(screen.getByText('Model: gemini-test')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /open video/i })).toHaveAttribute(
      'href',
      'https://www.youtube.com/watch?v=yt1',
    )
  })

  it('renders a notes rail with a stage label per row and a sentence-case heading', async () => {
    setup({ videos: [makeVideo(1), makeVideo(2, { stage: 'approved' })] })
    renderRoute('/playlists/1/review/1')

    const rail = await screen.findByRole('complementary', { name: /videos in this playlist/i })
    expect(await within(rail).findByText('Videos (2)')).not.toHaveClass('uppercase')
    expect(within(rail).getByRole('img', { name: 'Needs review' })).toBeInTheDocument()
    expect(within(rail).getByRole('img', { name: 'Approved' })).toBeInTheDocument()
  })

  it('finds videos beyond the first 250 (pages until exhausted)', async () => {
    const many = Array.from({ length: 600 }, (_, i) => makeVideo(i + 1, { stage: 'approved' }))
    const api = setup({ videos: many })
    renderRoute('/playlists/1/review/580')

    expect(
      await screen.findByRole('heading', { level: 1, name: '#580 Video 580' }),
    ).toBeInTheDocument()
    expect(api.callsTo('GET', '/api/playlists/1/videos').length).toBeGreaterThanOrEqual(2)
  })
})

describe('ReviewScreen approve', () => {
  it('Approve saves the dirty draft first, then approves, then opens the next video', async () => {
    const api = setup()
    api.on(
      'PUT',
      '/api/videos/1/summary',
      makeSummary(1, { content: '# Notes 1 more', edited: true }),
    )
    api.on('POST', '/api/videos/1/approve', makeVideo(1, { stage: 'approved' }))
    const user = userEvent.setup()
    const { router } = renderRoute('/playlists/1/review/1')

    await user.type(await openEditor(user), ' more')
    await user.click(screen.getByRole('button', { name: 'Save and approve' }))

    await waitFor(() => expect(api.callsTo('POST', '/api/videos/1/approve')).toHaveLength(1))
    const order = api.calls.map((call) => `${call.method} ${call.path}`)
    expect(order.indexOf('PUT /api/videos/1/summary')).toBeGreaterThan(-1)
    expect(order.indexOf('PUT /api/videos/1/summary')).toBeLessThan(
      order.indexOf('POST /api/videos/1/approve'),
    )
    expect(api.callsTo('PUT', '/api/videos/1/summary')[0].body).toEqual({
      content: '# Notes 1 more',
    })
    await waitFor(() => expect(router.state.location.pathname).toBe('/playlists/1/review/2'))
  })

  it('does not approve or navigate when saving fails, and keeps the draft', async () => {
    const api = setup()
    api.on('PUT', '/api/videos/1/summary', {
      status: 500,
      body: { code: 'server_error', message: 'Disk full' },
    })
    api.on('POST', '/api/videos/1/approve', makeVideo(1, { stage: 'approved' }))
    const user = userEvent.setup()
    const { router } = renderRoute('/playlists/1/review/1')

    const textarea = await openEditor(user)
    await user.type(textarea, ' more')
    await user.click(screen.getByRole('button', { name: 'Save and approve' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Disk full')
    expect(api.callsTo('POST', '/api/videos/1/approve')).toHaveLength(0)
    expect(router.state.location.pathname).toBe('/playlists/1/review/1')
    expect(textarea).toHaveValue('# Notes 1 more')
    expect(screen.getByText('Unsaved changes')).toBeInTheDocument()
  })

  it('Approve without edits just approves', async () => {
    const api = setup()
    api.on('POST', '/api/videos/1/approve', makeVideo(1, { stage: 'approved' }))
    const user = userEvent.setup()
    renderRoute('/playlists/1/review/1')

    await user.click(await screen.findByRole('button', { name: 'Approve' }))

    await waitFor(() => expect(api.callsTo('POST', '/api/videos/1/approve')).toHaveLength(1))
    expect(api.callsTo('PUT', '/api/videos/1/summary')).toHaveLength(0)
  })
})

describe('ReviewScreen unsaved changes', () => {
  it('asks before leaving via the rail, can be cancelled, and discards on confirm', async () => {
    setup()
    const user = userEvent.setup()
    const { router } = renderRoute('/playlists/1/review/1')

    await user.type(await openEditor(user), ' more')
    await user.click(screen.getByRole('link', { name: /Video 2/ }))

    let dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('Discard unsaved changes to #001?')
    await user.click(within(dialog).getByRole('button', { name: 'Keep editing' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(router.state.location.pathname).toBe('/playlists/1/review/1')

    await user.click(screen.getByRole('link', { name: /Video 2/ }))
    dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Discard changes' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/playlists/1/review/2'))
  })

  it('also guards the breadcrumb, the top bar and browser back', async () => {
    setup()
    const user = userEvent.setup()
    const { router } = renderRoute('/playlists/1/review/1')

    await user.type(await openEditor(user), ' more')

    await user.click(screen.getByRole('link', { name: 'Settings' }))
    expect(await screen.findByRole('dialog')).toHaveTextContent('Discard unsaved changes')
    await user.click(screen.getByRole('button', { name: 'Keep editing' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    await user.click(
      within(screen.getByRole('navigation', { name: 'Breadcrumb' })).getByRole('link', {
        name: 'Playlists',
      }),
    )
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Keep editing' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(router.state.location.pathname).toBe('/playlists/1/review/1')
  })

  it('does not interrupt navigation when there is nothing unsaved', async () => {
    setup()
    const user = userEvent.setup()
    const { router } = renderRoute('/playlists/1/review/1')

    await screen.findByRole('heading', { level: 1, name: /#001/ })
    await user.click(await screen.findByRole('link', { name: /Video 2/ }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/playlists/1/review/2'))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('registers a beforeunload warning only while there are unsaved edits', async () => {
    setup()
    const user = userEvent.setup()
    renderRoute('/playlists/1/review/1')

    const textarea = await openEditor(user)
    const clean = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(clean)
    expect(clean.defaultPrevented).toBe(false)

    await user.type(textarea, ' more')
    const dirty = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(dirty)
    expect(dirty.defaultPrevented).toBe(true)
  })
})

describe('ReviewScreen keyboard shortcuts', () => {
  it('Ctrl+S saves case-insensitively, ignores key repeat and ignores events while a dialog is open', async () => {
    const api = setup()
    api.on('PUT', '/api/videos/1/summary', makeSummary(1, { content: '# Notes 1 more' }))
    const user = userEvent.setup()
    renderRoute('/playlists/1/review/1')

    await user.type(await openEditor(user), ' more')

    // Key repeat is ignored.
    fireEvent.keyDown(window, { key: 'S', ctrlKey: true, repeat: true })
    expect(api.callsTo('PUT', '/api/videos/1/summary')).toHaveLength(0)

    // A dialog owns the keyboard while it is open.
    await user.click(screen.getByRole('button', { name: /regenerate/i }))
    await screen.findByRole('dialog')
    fireEvent.keyDown(window, { key: 's', ctrlKey: true })
    expect(api.callsTo('PUT', '/api/videos/1/summary')).toHaveLength(0)
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    // Upper-case S (Caps Lock) works.
    fireEvent.keyDown(window, { key: 'S', ctrlKey: true })
    await waitFor(() => expect(api.callsTo('PUT', '/api/videos/1/summary')).toHaveLength(1))
  })

  it('Ctrl+Enter saves then approves', async () => {
    const api = setup()
    api.on('PUT', '/api/videos/1/summary', makeSummary(1, { content: '# Notes 1 more' }))
    api.on('POST', '/api/videos/1/approve', makeVideo(1, { stage: 'approved' }))
    const user = userEvent.setup()
    renderRoute('/playlists/1/review/1')

    await user.type(await openEditor(user), ' more')
    fireEvent.keyDown(window, { key: 'Enter', ctrlKey: true })

    await waitFor(() => expect(api.callsTo('POST', '/api/videos/1/approve')).toHaveLength(1))
    expect(api.callsTo('PUT', '/api/videos/1/summary')).toHaveLength(1)
  })
})

describe('ReviewScreen notes states', () => {
  it('shows "Notes not generated yet" for a 404 instead of retrying or erroring', async () => {
    const api = setup()
    api.on('GET', '/api/videos/1/summary', {
      status: 404,
      body: { code: 'not_found', message: 'Summary not found for video' },
    })
    renderRoute('/playlists/1/review/1')

    expect(await screen.findByText('Notes not generated yet.')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(api.callsTo('GET', '/api/videos/1/summary')).toHaveLength(1)
  })

  it('shows a distinct error with Retry when the notes fail to load', async () => {
    const api = setup()
    api.on('GET', '/api/videos/1/summary', {
      status: 500,
      body: { code: 'server_error', message: 'Database down' },
    })
    renderRoute('/playlists/1/review/1')

    // 5xx errors are retried a couple of times (with backoff) before the error shows.
    expect(await screen.findByRole('alert', {}, { timeout: 8000 })).toHaveTextContent(
      'Could not load the notes',
    )
    expect(screen.queryByText('Notes not generated yet.')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  }, 20000)

  it('exposes Preview / Edit as tabs and labels the editor', async () => {
    setup()
    const user = userEvent.setup()
    renderRoute('/playlists/1/review/1')

    const preview = await screen.findByRole('tab', { name: 'Preview' })
    expect(preview).toHaveAttribute('aria-selected', 'true')
    await user.click(screen.getByRole('tab', { name: 'Edit' }))
    expect(screen.getByRole('tab', { name: 'Edit' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('textbox', { name: 'Edit notes (Markdown)' })).toBeInTheDocument()
  })

  it('switches Preview / Edit with the arrow keys (roving focus)', async () => {
    setup()
    const user = userEvent.setup()
    renderRoute('/playlists/1/review/1')

    const preview = await screen.findByRole('tab', { name: 'Preview' })
    expect(screen.getByRole('tab', { name: 'Edit' })).toHaveAttribute('tabindex', '-1')
    preview.focus()
    await user.keyboard('{ArrowRight}')
    const edit = screen.getByRole('tab', { name: 'Edit' })
    expect(edit).toHaveFocus()
    expect(edit).toHaveAttribute('aria-selected', 'true')
  })
})

describe('ReviewScreen "Edited since publishing"', () => {
  const published = [makeVideo(1, { stage: 'published' })]

  it('is shown when the backend says the notes changed since publishing', async () => {
    setup({ videos: published, summary: { edited: true, needs_republish: true } })
    renderRoute('/playlists/1/review/1')

    expect(await screen.findByText('Edited since publishing')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Re-publish' })).toBeInTheDocument()
  })

  it('is NOT shown for an edited note that matches what was published', async () => {
    setup({ videos: published, summary: { edited: true, needs_republish: false } })
    renderRoute('/playlists/1/review/1')

    await screen.findByRole('heading', { level: 1, name: /#001/ })
    await screen.findByText('Notes 1')
    expect(screen.queryByText('Edited since publishing')).not.toBeInTheDocument()
  })

  it('disappears once the background Re-publish has finished', async () => {
    let status = 'published'
    let needsRepublish = true
    const api = setup({ videos: published })
    api.on('GET', '/api/playlists/1', () => makePlaylist({ status }))
    api.on('GET', '/api/videos/1/summary', () =>
      makeSummary(1, { edited: true, needs_republish: needsRepublish }),
    )
    api.on('POST', '/api/playlists/1/publish', () => {
      status = 'publishing'
      return PUBLISH_ACCEPTED
    })
    const user = userEvent.setup()
    renderRoute('/playlists/1/review/1')

    await user.click(await screen.findByRole('button', { name: 'Re-publish' }))
    expect(await screen.findByRole('button', { name: /publishing/i })).toBeDisabled()

    status = 'published'
    needsRepublish = false
    await waitFor(
      () => expect(screen.queryByText('Edited since publishing')).not.toBeInTheDocument(),
      { timeout: 6000 },
    )
  }, 15000)
})

describe('ReviewScreen actions', () => {
  it('Regenerate confirms with the overwrite warning, then queues the regeneration', async () => {
    let stage: Video['stage'] = 'pending_review'
    const api = setup()
    api.on('GET', '/api/playlists/1/videos', () =>
      videoList([makeVideo(1, { stage }), makeVideo(2)]),
    )
    api.on('POST', '/api/videos/1/regenerate', () => {
      stage = 'queued'
      return { status: 202, body: makeVideo(1, { stage: 'queued', regenerating: true }) }
    })
    const user = userEvent.setup()
    renderRoute('/playlists/1/review/1')

    await user.click(await screen.findByRole('button', { name: /regenerate/i }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: 'Regenerate notes' })).toBeInTheDocument()
    expect(dialog).toHaveTextContent('including any edits you made, will be overwritten')
    await user.click(within(dialog).getByRole('button', { name: 'Regenerate notes' }))

    await waitFor(() => expect(api.callsTo('POST', '/api/videos/1/regenerate')).toHaveLength(1))
    // 202 only means "queued": no success toast until the video settles.
    expect(await screen.findByRole('button', { name: /regenerating/i })).toBeDisabled()
    expect(screen.queryByText('Notes regenerated.')).not.toBeInTheDocument()
  })

  it('handles an async (202) regenerate: shows Regenerating, polls, then reports completion', async () => {
    let stage: Video['stage'] = 'pending_review'
    const api = setup()
    api.on('GET', '/api/playlists/1/videos', () =>
      videoList([makeVideo(1, { stage }), makeVideo(2)]),
    )
    api.on('POST', '/api/videos/1/regenerate', () => {
      stage = 'generating_notes'
      return { status: 202, body: makeVideo(1, { stage: 'queued', regenerating: true }) }
    })
    const user = userEvent.setup()
    renderRoute('/playlists/1/review/1')

    await user.click(await screen.findByRole('button', { name: /^regenerate$/i }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Regenerate notes' }))

    expect(await screen.findByRole('button', { name: /regenerating/i })).toBeDisabled()

    stage = 'pending_review' // the 3s poll sees it finished
    expect(await screen.findByText('Notes regenerated.', {}, { timeout: 8000 })).toBeInTheDocument()
  }, 20000)

  it('shows the failure reason, retry state and a Retry button for a failed video', async () => {
    const api = setup({
      videos: [
        makeVideo(1, {
          stage: 'failed',
          failure_type: 'rate_limited',
          failure_kind: 'transient',
          failure_reason: 'Rate limited by YouTube',
          retry_count: 5,
          max_auto_retries: 5,
        }),
      ],
    })
    api.on('GET', /\/api\/videos\/1\/summary/, {
      status: 404,
      body: { code: 'NOT_FOUND', message: 'Summary not found for video', details: {} },
    })
    api.on('POST', '/api/videos/1/retry', makeVideo(1, { stage: 'queued' }))
    const user = userEvent.setup()
    renderRoute('/playlists/1/review/1')

    expect(
      await screen.findByText(
        'Rate limited by YouTube · Gave up after 5/5 automatic retries - use Retry',
      ),
    ).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(api.callsTo('POST', '/api/videos/1/retry')).toHaveLength(1))
    expect(await screen.findByText('#001 queued for retry.')).toBeInTheDocument()
  })

  it('clears the transcript of this video after confirmation (FR11)', async () => {
    const api = setup()
    api.on('DELETE', '/api/videos/1/transcript', { status: 'ok', message: 'cleared' })
    const user = userEvent.setup()
    renderRoute('/playlists/1/review/1')

    await screen.findByRole('heading', { level: 1, name: /#001/ })
    await user.click(await screen.findByRole('button', { name: 'More actions' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Clear transcript' }))

    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('Delete the stored transcript for #001 "Video 1"?')
    await user.click(within(dialog).getByRole('button', { name: 'Clear transcript' }))

    await waitFor(() => expect(api.callsTo('DELETE', '/api/videos/1/transcript')).toHaveLength(1))
  })

  it('disables "Next needing review" with a reason when nothing else needs review', async () => {
    setup({ videos: [makeVideo(1), makeVideo(2, { stage: 'approved' })] })
    renderRoute('/playlists/1/review/1')

    const next = await screen.findByRole('button', { name: /next needing review/i })
    await waitFor(() => expect(next).toBeDisabled())
    expect(screen.getAllByRole('tooltip').map((node) => node.textContent)).toContain(
      'No other videos need review',
    )
  })
})

describe('ReviewScreen focus mode', () => {
  it('hides the breadcrumb and rail, shows a slim bar, and exits with Esc', async () => {
    setup()
    const user = userEvent.setup()
    renderRoute('/playlists/1/review/1')

    await screen.findByRole('heading', { level: 1, name: '#001 Video 1' })
    await user.click(screen.getByRole('button', { name: 'Focus mode' }))

    expect(screen.queryByRole('navigation', { name: 'Breadcrumb' })).not.toBeInTheDocument()
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: '#001 Video 1' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument()
    expect(screen.queryByText(/^Model:/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Previous video' })).toBeDisabled()

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(await screen.findByRole('navigation', { name: 'Breadcrumb' })).toBeInTheDocument()
  })

  it('toggles with F, ignores F while typing, and persists the choice', async () => {
    setup()
    const user = userEvent.setup()
    const first = renderRoute('/playlists/1/review/1')

    const textarea = await openEditor(user)
    await user.type(textarea, 'f')
    expect(screen.getByRole('navigation', { name: 'Breadcrumb' })).toBeInTheDocument()

    textarea.blur()
    fireEvent.keyDown(document.body, { key: 'f' })
    expect(screen.queryByRole('navigation', { name: 'Breadcrumb' })).not.toBeInTheDocument()
    expect(window.localStorage.getItem('atlas.review.focus')).toBe('1')

    first.unmount()
    renderRoute('/playlists/1/review/1')
    await screen.findByRole('heading', { level: 1, name: '#001 Video 1' })
    expect(screen.queryByRole('navigation', { name: 'Breadcrumb' })).not.toBeInTheDocument()
  })

  it('keeps the unsaved draft when toggling focus mode', async () => {
    setup()
    const user = userEvent.setup()
    renderRoute('/playlists/1/review/1')

    await user.type(await openEditor(user), ' more')
    await user.click(screen.getByRole('button', { name: 'Focus mode' }))

    expect(screen.getByRole('textbox', { name: /edit notes/i })).toHaveValue('# Notes 1 more')
    expect(screen.getByText('Unsaved changes')).toBeInTheDocument()
  })

  it('moves between videos with the previous / next buttons', async () => {
    setup()
    const user = userEvent.setup()
    const { router } = renderRoute('/playlists/1/review/1')

    await screen.findByRole('heading', { level: 1, name: /#001/ })
    await user.click(screen.getByRole('button', { name: 'Focus mode' }))
    await user.click(screen.getByRole('button', { name: 'Next video' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/playlists/1/review/2'))
  })
})

describe('ReviewScreen reading width and fullscreen', () => {
  it('uses the wide preview column with no toggle', async () => {
    setup()
    renderRoute('/playlists/1/review/1')

    const column = (await screen.findByText('Notes 1')).closest('.mx-auto')
    expect(column).toHaveClass('max-w-5xl')
    expect(screen.queryByRole('button', { name: 'Wide column' })).not.toBeInTheDocument()
  })

  it('hides the fullscreen button when the browser cannot do it', async () => {
    setup()
    renderRoute('/playlists/1/review/1')

    await screen.findByRole('heading', { level: 1, name: /#001/ })
    expect(screen.queryByRole('button', { name: 'Browser fullscreen' })).not.toBeInTheDocument()
  })

  it('enters focus mode and requests browser fullscreen', async () => {
    setup()
    const request = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(document, 'fullscreenEnabled', { value: true, configurable: true })
    document.documentElement.requestFullscreen = request
    try {
      const user = userEvent.setup()
      renderRoute('/playlists/1/review/1')

      await user.click(await screen.findByRole('button', { name: 'Browser fullscreen' }))

      expect(request).toHaveBeenCalledTimes(1)
      expect(screen.queryByRole('navigation', { name: 'Breadcrumb' })).not.toBeInTheDocument()
    } finally {
      Reflect.deleteProperty(document, 'fullscreenEnabled')
      Reflect.deleteProperty(document.documentElement, 'requestFullscreen')
    }
  })
})
