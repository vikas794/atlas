import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { mockApi, renderRoute } from '../../test/harness'
import type { DiagnosticsResponse } from '../../lib/types'

const DIAGNOSTICS: DiagnosticsResponse = {
  checks: [
    { id: 'database', label: 'Database', status: 'ok', message: 'SQLite at data/atlas.db' },
    {
      id: 'drive',
      label: 'Google Drive',
      status: 'warning',
      message: 'Not authorized yet: publishing is unavailable.',
    },
    { id: 'llm', label: 'Notes model', status: 'error', message: 'GEMINI_API_KEY is not set.' },
  ],
  recent_errors: [
    {
      timestamp: '2026-10-12T09:30:00Z',
      level: 'ERROR',
      logger: 'src.application.worker',
      message: 'Video 12 failed: rate_limited',
    },
  ],
}

describe('DiagnosticsScreen', () => {
  it('shows every check with an icon, a text status and its message', async () => {
    mockApi().on('GET', '/api/diagnostics', DIAGNOSTICS)
    renderRoute('/diagnostics')

    const list = await screen.findByRole('list', { name: 'Diagnostic checks' })
    const rows = within(list).getAllByRole('listitem')
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent('Database')
    expect(rows[0]).toHaveTextContent('OK')
    expect(rows[1]).toHaveTextContent('Warning')
    expect(rows[1]).toHaveTextContent('Not authorized yet: publishing is unavailable.')
    expect(rows[2]).toHaveTextContent('Error')
    expect(rows[2]).toHaveTextContent('GEMINI_API_KEY is not set.')
    // Never colour alone: each row carries an icon next to the text status.
    for (const row of rows) expect(row.querySelector('svg')).not.toBeNull()
    expect(screen.getByText('1 OK, 1 warning, 1 error')).toBeInTheDocument()
  })

  it('lists the recent backend errors', async () => {
    mockApi().on('GET', '/api/diagnostics', DIAGNOSTICS)
    renderRoute('/diagnostics')

    const row = (await screen.findByText('Video 12 failed: rate_limited')).closest('tr')!
    expect(within(row).getByText('Error')).toBeInTheDocument()
    expect(within(row).getByText('src.application.worker')).toBeInTheDocument()
    expect(within(row).getByRole('time')).toHaveAttribute('title')
  })

  it('keeps the backend order (newest first) and says what is listed', async () => {
    mockApi().on('GET', '/api/diagnostics', {
      ...DIAGNOSTICS,
      recent_errors: [
        { timestamp: '2026-10-12T09:30:00Z', level: 'WARNING', logger: 'a', message: 'newest' },
        { timestamp: '2026-10-12T10:30:00Z', level: 'ERROR', logger: 'b', message: 'clock skew' },
        { timestamp: '2026-10-12T08:00:00Z', level: 'ERROR', logger: 'c', message: 'oldest' },
      ],
    })
    renderRoute('/diagnostics')

    await screen.findByText('newest')
    const table = screen.getByRole('table')
    const messages = within(table)
      .getAllByRole('row')
      .slice(1)
      .map((row) => row.lastElementChild?.textContent)
    expect(messages).toEqual(['newest', 'clock skew', 'oldest'])
    expect(
      screen.getByRole('heading', { level: 2, name: 'Recent warnings and errors' }),
    ).toBeInTheDocument()
    expect(screen.getByText(/WARNING or above, newest first/)).toBeInTheDocument()
  })

  const MIXED: DiagnosticsResponse = {
    ...DIAGNOSTICS,
    recent_errors: [
      { timestamp: '2026-10-12T09:30:00Z', level: 'WARNING', logger: 'a', message: 'slow call' },
      { timestamp: '2026-10-12T09:00:00Z', level: 'ERROR', logger: 'b', message: 'db locked' },
    ],
  }

  it('filters the log by warnings and errors', async () => {
    mockApi().on('GET', '/api/diagnostics', MIXED)
    const user = userEvent.setup()
    renderRoute('/diagnostics')

    await screen.findByText('slow call')
    await user.click(screen.getByRole('button', { name: 'Errors (1)' }))
    expect(screen.queryByText('slow call')).not.toBeInTheDocument()
    expect(screen.getByText('db locked')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Warnings (1)' }))
    expect(screen.getByText('slow call')).toBeInTheDocument()
    expect(screen.queryByText('db locked')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'All (2)' }))
    expect(screen.getByText('slow call')).toBeInTheDocument()
    expect(screen.getByText('db locked')).toBeInTheDocument()
  })

  it('clears the log through the backend', async () => {
    const api = mockApi().on('GET', '/api/diagnostics', MIXED)
    api.on('DELETE', '/api/diagnostics/recent-errors', { status: 204 })
    const user = userEvent.setup()
    renderRoute('/diagnostics')
    await screen.findByText('slow call')

    await user.click(screen.getByRole('button', { name: 'Clear log' }))
    expect(await screen.findByText('No warnings or errors logged recently.')).toBeInTheDocument()
    expect(screen.queryByText('slow call')).not.toBeInTheDocument()
  })

  it('copies the problems and the filtered log to the clipboard', async () => {
    mockApi().on('GET', '/api/diagnostics', MIXED)
    const user = userEvent.setup()
    renderRoute('/diagnostics')
    await screen.findByText('slow call')

    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })

    await user.click(screen.getByRole('button', { name: 'Errors (1)' }))
    await user.click(screen.getByRole('button', { name: 'Copy for fixing' }))

    expect(writeText).toHaveBeenCalledTimes(1)
    const text = writeText.mock.calls[0][0] as string
    expect(text).toContain('[ERROR] Notes model: GEMINI_API_KEY is not set.')
    expect(text).toContain('[WARNING] Google Drive')
    expect(text).not.toContain('Database')
    expect(text).toContain('ERROR b: db locked')
    expect(text).not.toContain('slow call')
    expect(await screen.findByRole('button', { name: 'Copied' })).toBeInTheDocument()
  })

  it('says so when there are no recent errors', async () => {
    mockApi().on('GET', '/api/diagnostics', { ...DIAGNOSTICS, recent_errors: [] })
    renderRoute('/diagnostics')

    expect(await screen.findByText('No warnings or errors logged recently.')).toBeInTheDocument()
  })

  it('explains a missing endpoint and offers Retry when the request fails', async () => {
    const api = mockApi().on('GET', '/api/diagnostics', {
      status: 404,
      body: { code: 'HTTP_404', message: 'Not Found', details: {} },
    })
    const user = userEvent.setup()
    renderRoute('/diagnostics')

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Could not load diagnostics')
    expect(alert).toHaveTextContent('GET /api/diagnostics returned 404')

    api.on('GET', '/api/diagnostics', DIAGNOSTICS)
    await user.click(within(alert).getByRole('button', { name: 'Retry' }))
    expect(await screen.findByRole('list', { name: 'Diagnostic checks' })).toBeInTheDocument()
  })

  it('shows the backend error message for a server failure', async () => {
    mockApi().on('GET', '/api/diagnostics', {
      status: 503,
      body: { code: 'CONFIG_MISSING', message: 'Database path is not writable', details: {} },
    })
    renderRoute('/diagnostics')

    // 503 is retried twice (with backoff) before the error state appears.
    expect(
      await screen.findByText(/Database path is not writable/, {}, { timeout: 10000 }),
    ).toBeInTheDocument()
  }, 20000)

  it('is reachable from the top bar', async () => {
    mockApi()
      .on('GET', '/api/playlists', { playlists: [], total: 0 })
      .on('GET', '/api/diagnostics', DIAGNOSTICS)
    const user = userEvent.setup()
    renderRoute('/')

    await user.click(await screen.findByRole('link', { name: 'Diagnostics' }))
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Diagnostics' }),
    ).toBeInTheDocument()
  })
})
