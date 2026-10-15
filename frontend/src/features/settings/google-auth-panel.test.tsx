import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { mockApi, renderRoute } from '../../test/harness'

function setup() {
  return mockApi()
    .on('GET', '/api/drive/status', {
      configured: false,
      state: 'credentials_missing',
      message: 'No OAuth client file found.',
    })
    .on('GET', '/api/usage', {
      total_requests: 0,
      total_input_tokens: 0,
      total_output_tokens: 0,
      total_tokens: 0,
      total_cost_usd: 0,
      cache_hit_rate: 0,
      by_provider: [],
      by_operation: [],
      cache_stats: null,
      time_range: null,
    })
}

describe('GoogleAuthPanel', () => {
  it('accepts the client_secret_*.json file Google hands out and confirms the upload', async () => {
    const api = setup().on('POST', '/api/drive/credentials', {
      status: 'success',
      message: 'Credentials uploaded successfully.',
    })
    const user = userEvent.setup()
    const { container } = renderRoute('/settings')

    expect(await screen.findByText('No OAuth client file found.')).toBeInTheDocument()
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    await user.upload(
      input,
      new File(['{}'], 'client_secret_123.apps.googleusercontent.com.json', {
        type: 'application/json',
      }),
    )

    await waitFor(() => expect(api.callsTo('POST', '/api/drive/credentials')).toHaveLength(1))
    expect(
      await screen.findByText('Google credentials uploaded. Next: authenticate.'),
    ).toBeInTheDocument()
  })

  it('reports a failed consent flow instead of staying silent', async () => {
    setup().on('POST', '/api/drive/auth', {
      status: 400,
      body: { code: 'DRIVE_AUTH_FAILED', message: 'Consent was denied', details: {} },
    })
    const user = userEvent.setup()
    renderRoute('/settings')

    await user.click(await screen.findByRole('button', { name: 'Authenticate account' }))

    expect(
      await screen.findByText('Could not connect Google Drive: Consent was denied'),
    ).toBeInTheDocument()
    expect(screen.getByText('Failed to authenticate: Consent was denied')).toBeInTheDocument()
  })

  it('collapses the setup steps behind an accordion once Drive is connected', async () => {
    mockApi().on('GET', '/api/drive/status', {
      configured: true,
      state: 'connected',
      message: 'Google Drive is connected.',
    })
    const user = userEvent.setup()
    renderRoute('/settings')

    const toggle = await screen.findByRole('button', { name: /Google Drive integration/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('button', { name: 'Authenticate account' })).not.toBeInTheDocument()

    await user.click(toggle)

    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('button', { name: 'Authenticate account' })).toBeVisible()
  })
})
