import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { ErrorBoundary, RouteErrorBoundary } from './error-boundary'
import App from '../../App'

function Boom(): never {
  throw new Error('Cannot read properties of undefined (reading "stage")')
}

describe('ErrorBoundary', () => {
  it('replaces a crashed tree with a readable fallback instead of a blank page', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    )

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('Something went wrong on this screen')
    expect(alert).toHaveTextContent('Reload the page to try again.')
    expect(screen.getByText(/reading "stage"/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reload page' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to playlists' })).toHaveAttribute('href', '/')
  })

  it('renders children untouched when nothing throws', () => {
    render(
      <ErrorBoundary>
        <p>All good</p>
      </ErrorBoundary>,
    )
    expect(screen.getByText('All good')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('RouteErrorBoundary', () => {
  it('keeps the top bar when a routed screen crashes', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const router = createMemoryRouter(
      [
        {
          element: <App />,
          children: [
            { errorElement: <RouteErrorBoundary />, children: [{ path: '/', element: <Boom /> }] },
          ],
        },
      ],
      { initialEntries: ['/'] },
    )
    render(
      <QueryClientProvider client={new QueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    )

    expect(await screen.findByText('Something went wrong on this screen')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Atlas home' })).toBeInTheDocument()
  })
})
