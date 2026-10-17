import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { makePlaylist, mockApi, renderRoute } from '../../test/harness'

const VALID_URL = 'https://www.youtube.com/playlist?list=PL1'

describe('PlaylistsHome', () => {
  it('shows an error with Retry (not the empty state) when the list fails to load', async () => {
    mockApi().on('GET', '/api/playlists', {
      status: 500,
      body: { code: 'server_error', message: 'Database down' },
    })
    renderRoute('/')

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load playlists')
    expect(screen.queryByText('No playlists yet.')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('shows the empty state and focuses the input when nothing is tracked', async () => {
    mockApi().on('GET', '/api/playlists', { playlists: [], total: 0 })
    renderRoute('/')

    expect(await screen.findByText('No playlists yet.')).toBeInTheDocument()
    await waitFor(() =>
      expect(screen.getByRole('textbox', { name: 'Paste a YouTube playlist link' })).toHaveFocus(),
    )
  })

  it('validates the link inline before calling the backend', async () => {
    const api = mockApi().on('GET', '/api/playlists', { playlists: [], total: 0 })
    const user = userEvent.setup()
    renderRoute('/')

    const input = await screen.findByRole('textbox', { name: 'Paste a YouTube playlist link' })
    await user.click(screen.getByRole('button', { name: 'Add playlist' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Paste a YouTube playlist link.')

    await user.click(input)
    await user.paste('https://example.com/list?list=PL1')
    await user.click(screen.getByRole('button', { name: 'Add playlist' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Only YouTube links are supported.')

    await user.clear(input)
    await user.click(input)
    await user.paste('https://www.youtube.com/watch?v=abc')
    await user.click(screen.getByRole('button', { name: 'Add playlist' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('no playlist')

    expect(api.callsTo('POST', '/api/playlists')).toHaveLength(0)
  })

  it('shows the server error inline when the backend rejects a link', async () => {
    mockApi()
      .on('GET', '/api/playlists', { playlists: [], total: 0 })
      .on('POST', '/api/playlists', {
        status: 422,
        body: { code: 'playlist_unavailable', message: 'This playlist is private.' },
      })
    const user = userEvent.setup()
    renderRoute('/')

    await user.click(await screen.findByRole('textbox', { name: 'Paste a YouTube playlist link' }))
    await user.paste(VALID_URL)
    await user.click(screen.getByRole('button', { name: 'Add playlist' }))

    const messages = await screen.findAllByText('This playlist is private.')
    expect(messages.length).toBeGreaterThan(0)
  })

  it('says "already tracked" (not "Added") when the link is a known playlist', async () => {
    mockApi()
      .on('GET', '/api/playlists', { playlists: [makePlaylist({ video_count: 3 })], total: 1 })
      .on('POST', '/api/playlists', { status: 200, body: makePlaylist({ video_count: 5 }) })
    const user = userEvent.setup()
    renderRoute('/')

    await screen.findByText('Complete Java')
    await user.click(screen.getByRole('textbox', { name: 'Paste a YouTube playlist link' }))
    await user.paste(VALID_URL)
    await user.click(screen.getByRole('button', { name: 'Add playlist' }))

    expect(
      await screen.findByText('"Complete Java" is already tracked: synced, 2 new videos.'),
    ).toBeInTheDocument()
    expect(screen.queryByText(/^Added/)).not.toBeInTheDocument()
  })

  it('lists playlists with sentence-case headings, last synced and a row actions menu', async () => {
    mockApi().on('GET', '/api/playlists', {
      playlists: [makePlaylist({ stage_counts: { pending_review: 2, failed: 1 } as never })],
      total: 1,
    })
    const user = userEvent.setup()
    renderRoute('/')

    expect(await screen.findByRole('link', { name: 'Complete Java' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Tracked playlists' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Add YouTube playlist' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Last synced' })).toBeInTheDocument()
    expect(document.querySelector('tbody time')).toHaveAttribute('title')

    await user.click(screen.getByRole('button', { name: 'Actions for Complete Java' }))
    for (const name of ['Open', 'Re-sync new videos', 'Reprocess', 'Delete']) {
      expect(await screen.findByRole('menuitem', { name })).toBeInTheDocument()
    }
  })

  it('re-sync has its own toast and leaves the add-playlist input alone', async () => {
    const api = mockApi()
      .on('GET', '/api/playlists', { playlists: [makePlaylist({ video_count: 3 })], total: 1 })
      .on('POST', '/api/playlists', makePlaylist({ video_count: 6 }))
    const user = userEvent.setup()
    renderRoute('/')

    const input = await screen.findByRole('textbox', { name: 'Paste a YouTube playlist link' })
    await user.click(input)
    await user.paste('half typed')

    await user.click(await screen.findByRole('button', { name: 'Actions for Complete Java' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Re-sync new videos' }))

    expect(await screen.findByText('Synced "Complete Java": 3 new videos.')).toBeInTheDocument()
    expect(screen.queryByText(/added successfully/i)).not.toBeInTheDocument()
    expect(input).toHaveValue('half typed')
    expect(api.callsTo('POST', '/api/playlists')[0].body).toEqual({
      url: makePlaylist().source_url,
    })
  })

  it('states the consequence and count before deleting', async () => {
    const api = mockApi()
      .on('GET', '/api/playlists', { playlists: [makePlaylist({ video_count: 187 })], total: 1 })
      .on('DELETE', '/api/playlists/1', { status: 'ok', message: 'deleted' })
    const user = userEvent.setup()
    renderRoute('/')

    await user.click(await screen.findByRole('button', { name: 'Actions for Complete Java' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Delete' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: 'Delete playlist' })).toBeInTheDocument()
    expect(dialog).toHaveTextContent('all 187 stored videos, transcripts and notes')
    expect(dialog).toHaveTextContent('This does not delete Google Docs already in Drive.')
    await user.click(within(dialog).getByRole('button', { name: 'Delete playlist' }))

    await waitFor(() => expect(api.callsTo('DELETE', '/api/playlists/1')).toHaveLength(1))
  })

  it('states the consequence and count before reprocessing', async () => {
    mockApi().on('GET', '/api/playlists', {
      playlists: [makePlaylist({ video_count: 12 })],
      total: 1,
    })
    const user = userEvent.setup()
    renderRoute('/')

    await user.click(await screen.findByRole('button', { name: 'Actions for Complete Java' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Reprocess' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: 'Reprocess playlist' })).toBeInTheDocument()
    expect(dialog).toHaveTextContent('All 12 videos go back to the queue')
  })

  it('retries every failed video across playlists from the list header', async () => {
    const api = mockApi()
      .on('GET', '/api/playlists', {
        playlists: [
          makePlaylist({ id: 1, stage_counts: { failed: 2 } as never }),
          makePlaylist({ id: 2, title: 'Other', stage_counts: { failed: 1 } as never }),
        ],
        total: 2,
      })
      .on('POST', '/api/videos/retry-failed', { status: 'ok', message: 'ok', count: 3 })
    const user = userEvent.setup()
    renderRoute('/')

    await user.click(await screen.findByRole('button', { name: 'Retry all failed (3)' }))

    expect(await screen.findByText('Queued 3 failed videos for retry.')).toBeInTheDocument()
    expect(api.callsTo('POST', '/api/videos/retry-failed')).toHaveLength(1)
  })

  it('disables Retry all failed when nothing has failed', async () => {
    mockApi().on('GET', '/api/playlists', { playlists: [makePlaylist()], total: 1 })
    renderRoute('/')

    expect(await screen.findByRole('button', { name: 'Retry all failed (0)' })).toBeDisabled()
    expect(screen.getByRole('tooltip')).toHaveTextContent('No failed videos')
  })
})
