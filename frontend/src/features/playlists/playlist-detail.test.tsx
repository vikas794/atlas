import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { makePlaylist, makeVideo, mockApi, renderRoute, videoList } from '../../test/harness'

const COUNTS = {
  queued: 1,
  fetching_transcript: 1,
  transcript_ready: 2,
  generating_notes: 1,
  pending_review: 2,
  approved: 0,
  published: 0,
  failed: 1,
}

function setup(options: { counts?: Partial<typeof COUNTS>; videos?: ReturnType<typeof makeVideo>[] } = {}) {
  const counts = { ...COUNTS, ...options.counts }
  const videos = options.videos ?? [makeVideo(1), makeVideo(2)]
  const api = mockApi()
  api.on('GET', '/api/playlists/1', () => makePlaylist({ video_count: 8, stage_counts: counts as never }))
  api.on('GET', '/api/playlists/1/videos', (call) => {
    const stage = call.search.get('stage')
    return videoList(stage ? videos.filter((video) => video.stage === stage) : videos)
  })
  return api
}

const VALID_STAGES = [
  null,
  'queued',
  'fetching_transcript',
  'transcript_ready',
  'generating_notes',
  'pending_review',
  'approved',
  'published',
  'failed',
]

describe('PlaylistDetail summary strip', () => {
  it('counts In progress as fetching + transcript_ready + generating and filters by the same set', async () => {
    const api = setup({
      videos: [
        makeVideo(1, { stage: 'fetching_transcript', position: 1 }),
        makeVideo(3, { stage: 'transcript_ready', position: 3 }),
        makeVideo(2, { stage: 'generating_notes', position: 2 }),
        makeVideo(4, { stage: 'approved', position: 4 }),
      ],
    })
    const user = userEvent.setup()
    renderRoute('/playlists/1')

    const card = await screen.findByRole('button', { name: /in progress/i })
    expect(within(card).getByText('4')).toBeInTheDocument()

    await user.click(card)

    // The table shows exactly the in-progress rows, merged in playlist order.
    await waitFor(() => expect(screen.getByText('Video 3')).toBeInTheDocument())
    expect(screen.queryByText('Video 4')).not.toBeInTheDocument()
    const stages = api.callsTo('GET', '/api/playlists/1/videos').map((call) => call.search.get('stage'))
    expect(stages).toEqual(
      expect.arrayContaining(['fetching_transcript', 'transcript_ready', 'generating_notes']),
    )
    // Never an invalid stage value (the backend 500s on those).
    for (const stage of stages) expect(VALID_STAGES).toContain(stage)
    const rows = screen
      .getAllByRole('row')
      .slice(1)
      .map((row) => row.textContent)
    expect(rows[0]).toContain('Video 1')
    expect(rows[1]).toContain('Video 2')
    expect(rows[2]).toContain('Video 3')
  })
})

describe('PlaylistDetail table', () => {
  it('shows zero-padded 1-based positions, an Updated column and a time tooltip', async () => {
    setup({ videos: [makeVideo(1, { position: 1 })] })
    renderRoute('/playlists/1')

    expect(await screen.findByText('001')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Updated' })).toBeInTheDocument()
    const time = document.querySelector('tbody time')
    expect(time).not.toBeNull()
    expect(time).toHaveAttribute('title')
    expect(time).toHaveAttribute('datetime')
  })

  it('opens Review when a row is clicked', async () => {
    setup()
    const user = userEvent.setup()
    const { router } = renderRoute('/playlists/1')

    const row = (await screen.findByText('Video 2')).closest('tr')!
    await user.click(row)
    await waitFor(() => expect(router.state.location.pathname).toBe('/playlists/1/review/2'))
  })

  it('opens Review with Enter on a focused row', async () => {
    setup()
    const user = userEvent.setup()
    const { router } = renderRoute('/playlists/1')

    const row = (await screen.findByText('Video 1')).closest('tr')!
    row.focus()
    await user.keyboard('{Enter}')
    await waitFor(() => expect(router.state.location.pathname).toBe('/playlists/1/review/1'))
  })

  it('Retry does not open the review and calls the retry endpoint', async () => {
    const api = setup({
      videos: [
        makeVideo(1, {
          stage: 'failed',
          failure_kind: 'permanent',
          failure_type: 'transcripts_disabled',
          failure_reason: 'Transcripts disabled by uploader',
        }),
      ],
    })
    api.on('POST', '/api/videos/1/retry', makeVideo(1, { stage: 'queued' }))
    const user = userEvent.setup()
    const { router } = renderRoute('/playlists/1')

    expect(await screen.findByText('Transcripts disabled by uploader')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /retry #001/i }))

    await waitFor(() => expect(api.callsTo('POST', '/api/videos/1/retry')).toHaveLength(1))
    expect(router.state.location.pathname).toBe('/playlists/1')
  })

  it('shows the retry countdown with attempt N/M for a scheduled transient retry', async () => {
    setup({
      videos: [
        makeVideo(1, {
          stage: 'failed',
          failure_kind: 'transient',
          failure_reason: 'Rate limited',
          retry_count: 3,
          max_auto_retries: 5,
          next_retry_at: new Date(Date.now() + 110_000).toISOString(),
        }),
      ],
    })
    renderRoute('/playlists/1')

    expect(await screen.findByText(/Retrying in 2m \(attempt 3\/5\)/)).toBeInTheDocument()
    expect(screen.getAllByText('Retrying').length).toBeGreaterThan(0)
  })

  it('shows "Failed – temporary" and "Retries exhausted" when no retry is scheduled', async () => {
    setup({
      videos: [
        makeVideo(1, {
          stage: 'failed',
          failure_kind: 'transient',
          failure_reason: 'Rate limited',
          retry_count: 5,
          next_retry_at: null,
        }),
      ],
    })
    renderRoute('/playlists/1')

    expect(await screen.findByText('Failed – temporary')).toBeInTheDocument()
    expect(screen.getByText('Retries exhausted - use Retry')).toBeInTheDocument()
    expect(screen.queryByText('Retrying')).not.toBeInTheDocument()
  })

  it('shows the edited tag and the removed-upstream badge next to the real stage', async () => {
    setup({ videos: [makeVideo(1, { stage: 'approved', edited: true, removed_upstream: true })] })
    renderRoute('/playlists/1')

    const row = (await screen.findByText('Video 1')).closest('tr')!
    expect(within(row).getByText('Approved')).toBeInTheDocument()
    expect(within(row).getByText('No longer in playlist')).toBeInTheDocument()
    expect(within(row).getByText('Edited')).toBeInTheDocument()
  })

  it('debounces the title search into a single request', async () => {
    const api = setup()
    const user = userEvent.setup()
    renderRoute('/playlists/1')
    await screen.findByText('Video 1')

    await user.type(screen.getByRole('searchbox', { name: /search videos/i }), 'abc')

    await waitFor(() =>
      expect(
        api.callsTo('GET', '/api/playlists/1/videos').some((call) => call.search.get('search') === 'abc'),
      ).toBe(true),
    )
    const searches = api.callsTo('GET', '/api/playlists/1/videos').map((call) => call.search.get('search'))
    expect(searches).not.toContain('a')
    expect(searches).not.toContain('ab')
  })

  it('keeps the previous rows on screen while a new filter loads', async () => {
    const api = setup()
    const user = userEvent.setup()
    renderRoute('/playlists/1')
    await screen.findByText('Video 1')

    // Make the next request slow; the old rows must stay visible meanwhile.
    api.on('GET', '/api/playlists/1/videos', async () => {
      await new Promise((resolve) => setTimeout(resolve, 200))
      return videoList([makeVideo(9)])
    })
    await user.click(screen.getByRole('button', { name: /needs review/i }))

    expect(screen.getByText('Video 1')).toBeInTheDocument()
    expect(document.querySelector('tbody .animate-pulse')).toBeNull()
    await waitFor(() => expect(screen.getByText('Video 9')).toBeInTheDocument())
  })
})

describe('PlaylistDetail states', () => {
  it('shows a not-found state for a 404 playlist instead of loading forever', async () => {
    mockApi().on('GET', '/api/playlists/1', {
      status: 404,
      body: { code: 'not_found', message: 'Playlist not found' },
    })
    renderRoute('/playlists/1')

    expect(await screen.findByRole('heading', { name: 'Playlist not found' })).toBeInTheDocument()
    expect(screen.queryByText(/loading/i)).not.toBeInTheDocument()
  })

  it('shows an error with Retry when the playlist fails to load', async () => {
    mockApi().on('GET', '/api/playlists/1', {
      status: 500,
      body: { code: 'server_error', message: 'Database down' },
    })
    renderRoute('/playlists/1')

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load this playlist')
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})

describe('PlaylistDetail actions', () => {
  it('explains why Approve all is disabled', async () => {
    setup({ counts: { pending_review: 0 } })
    renderRoute('/playlists/1')

    const button = await screen.findByRole('button', { name: /approve all/i })
    expect(button).toBeDisabled()
    expect(screen.getAllByRole('tooltip').map((node) => node.textContent)).toContain(
      'No videos are waiting for review',
    )
  })

  it('confirms Approve all with the count, then approves', async () => {
    const api = setup({ counts: { pending_review: 2 } })
    api.on('POST', '/api/playlists/1/approve-all', { status: 'ok', message: 'Approved 2 videos', count: 2 })
    const user = userEvent.setup()
    renderRoute('/playlists/1')

    await user.click(await screen.findByRole('button', { name: 'Approve all (2)' }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('Approve all 2 videos waiting for review')
    await user.click(within(dialog).getByRole('button', { name: 'Approve all (2)' }))

    await waitFor(() => expect(api.callsTo('POST', '/api/playlists/1/approve-all')).toHaveLength(1))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(await screen.findByText('Approved 2 videos.')).toBeInTheDocument()
  })

  it('keeps the dialog open and reports the error when the action fails (no unhandled rejection)', async () => {
    const api = setup({ counts: { pending_review: 2 } })
    api.on('POST', '/api/playlists/1/approve-all', {
      status: 500,
      body: { code: 'server_error', message: 'Boom' },
    })
    const user = userEvent.setup()
    renderRoute('/playlists/1')

    await user.click(await screen.findByRole('button', { name: 'Approve all (2)' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Approve all (2)' }))

    expect(await within(screen.getByRole('dialog')).findByRole('alert')).toHaveTextContent('Boom')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('states the consequence and count when deleting from the overflow menu', async () => {
    const api = setup()
    api.on('DELETE', '/api/playlists/1', { status: 'ok', message: 'Playlist deleted' })
    api.on('GET', '/api/playlists', { playlists: [], total: 0 })
    const user = userEvent.setup()
    const { router } = renderRoute('/playlists/1')

    await user.click(await screen.findByRole('button', { name: 'More actions' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Delete' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: 'Delete playlist' })).toBeInTheDocument()
    expect(dialog).toHaveTextContent('Delete "Complete Java" and all 8 stored videos, transcripts and notes?')
    expect(dialog).toHaveTextContent('This does not delete Google Docs already in Drive.')

    await user.click(within(dialog).getByRole('button', { name: 'Delete playlist' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/'))
  })

  it('offers the overflow actions from the design', async () => {
    setup()
    const user = userEvent.setup()
    renderRoute('/playlists/1')

    await user.click(await screen.findByRole('button', { name: 'More actions' }))
    for (const name of ['Re-sync new videos', 'Reprocess', 'Clean transcripts', 'Delete']) {
      expect(await screen.findByRole('menuitem', { name })).toBeInTheDocument()
    }
  })

  it('re-sync reports the number of new videos without saying "added"', async () => {
    const api = setup()
    api.on('POST', '/api/playlists', makePlaylist({ video_count: 11 }))
    const user = userEvent.setup()
    renderRoute('/playlists/1')

    await user.click(await screen.findByRole('button', { name: 'More actions' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Re-sync new videos' }))

    expect(await screen.findByText('Synced "Complete Java": 3 new videos.')).toBeInTheDocument()
  })

  it('clears one video transcript after confirmation (FR11)', async () => {
    const api = setup({ videos: [makeVideo(1)] })
    api.on('DELETE', '/api/videos/1/transcript', { status: 'ok', message: 'cleared' })
    const user = userEvent.setup()
    renderRoute('/playlists/1')

    await user.click(await screen.findByRole('button', { name: 'More actions for #001' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Clear transcript' }))

    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('Delete the stored transcript for #001 "Video 1"?')
    expect(api.callsTo('DELETE', '/api/videos/1/transcript')).toHaveLength(0)
    await user.click(within(dialog).getByRole('button', { name: 'Clear transcript' }))

    await waitFor(() => expect(api.callsTo('DELETE', '/api/videos/1/transcript')).toHaveLength(1))
    expect(await screen.findByText('Cleared the transcript for #001.')).toBeInTheDocument()
  })
})

describe('PlaylistDetail publishing', () => {
  it('says "Nothing new to publish" for a sync publish that did nothing', async () => {
    const api = setup({ counts: { approved: 1 } })
    api.on('POST', '/api/playlists/1/publish', {
      folder_id: 'f',
      created: 0,
      updated: 0,
      skipped: 3,
      status: 'ok',
    })
    const user = userEvent.setup()
    renderRoute('/playlists/1')

    await user.click(await screen.findByRole('button', { name: 'Publish approved to Drive' }))

    expect(await screen.findByText('Nothing new to publish.')).toBeInTheDocument()
    expect(screen.queryByText(/Published 0/)).not.toBeInTheDocument()
  })

  it('reports real counts for a sync publish', async () => {
    const api = setup({ counts: { approved: 1 } })
    api.on('POST', '/api/playlists/1/publish', {
      folder_id: 'f',
      created: 2,
      updated: 1,
      skipped: 0,
      status: 'ok',
    })
    const user = userEvent.setup()
    renderRoute('/playlists/1')

    await user.click(await screen.findByRole('button', { name: 'Publish approved to Drive' }))

    expect(await screen.findByText('Published 2 new Docs, updated 1 Doc in Drive.')).toBeInTheDocument()
  })

  it('handles an async (202) publish: shows Publishing, polls, then toasts the failure', async () => {
    const api = mockApi()
    let phase: 'ready' | 'publishing' | 'publish_failed' = 'ready'
    api.on('GET', '/api/playlists/1', () =>
      makePlaylist({
        status: phase,
        last_publish_error: phase === 'publish_failed' ? 'Drive quota exceeded' : null,
        stage_counts: { ...COUNTS, approved: 2 } as never,
      }),
    )
    api.on('GET', '/api/playlists/1/videos', videoList([makeVideo(1)]))
    api.on('POST', '/api/playlists/1/publish', () => {
      phase = 'publishing'
      return { status: 202, body: { status: 'accepted', message: 'Publishing started' } }
    })
    const user = userEvent.setup()
    renderRoute('/playlists/1')

    await user.click(await screen.findByRole('button', { name: 'Publish approved to Drive' }))
    expect(await screen.findByRole('button', { name: /publishing/i })).toBeDisabled()

    phase = 'publish_failed' // the next poll (3s) sees the failure
    const alerts = await screen.findAllByText('Drive quota exceeded', {}, { timeout: 6000 })
    expect(alerts.length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: 'Publish approved to Drive' })).toBeEnabled()
  }, 15000)
})
