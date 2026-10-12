import { screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import { mockApi, renderRoute } from './test/harness'

describe('router shell', () => {
  beforeEach(() => {
    // Every list endpoint is empty; this test only checks the frame and headings.
    mockApi().on('GET', '/api/playlists', { playlists: [], total: 0 })
  })

  it('renders the playlists home inside the top bar frame', async () => {
    renderRoute('/')

    // Route screens are code-split, so the heading appears once the chunk has loaded.
    expect(await screen.findByRole('heading', { level: 1, name: 'Playlists' })).toBeInTheDocument()
    expect(screen.getByRole('banner')).toBeInTheDocument()
    expect(screen.getAllByText('Atlas').length).toBeGreaterThan(0)
  })

  it('renders the settings screen', async () => {
    renderRoute('/settings')

    expect(await screen.findByRole('heading', { level: 1, name: 'Settings' })).toBeInTheDocument()
  })

  it('renders a not-found screen for an unknown route', () => {
    renderRoute('/nope')

    expect(screen.getByRole('heading', { level: 1, name: 'Page not found' })).toBeInTheDocument()
  })
})
