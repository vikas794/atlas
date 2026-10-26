import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { LlmKey } from '../../lib/types'
import { mockApi, renderRoute } from '../../test/harness'

function key(id: number, overrides: Partial<LlmKey> = {}): LlmKey {
  return {
    id,
    provider: 'gemini',
    label: `Key ${id}`,
    masked_key: 'AIza...wxyz',
    status: 'healthy',
    enabled: true,
    consecutive_failures: 0,
    last_checked_at: '2026-10-10T12:00:00Z',
    last_healthy_at: '2026-10-10T12:00:00Z',
    last_health_error: null,
    monthly_request_cap: null,
    monthly_token_cap: null,
    cooldowns: [],
    auto_disabled: false,
    ...overrides,
  }
}

const PROVIDERS = {
  providers: [
    { name: 'gemini', label: 'Gemini', default_api_base: null, key_count: 2, usable_key_count: 2 },
    { name: 'nvidia', label: 'NVIDIA', default_api_base: null, key_count: 1, usable_key_count: 0 },
  ],
}

function setup(keys: LlmKey[] = [key(1), key(2), key(3, { provider: 'nvidia' })]) {
  return mockApi().on('GET', '/api/llm/keys', { keys }).on('GET', '/api/llm/providers', PROVIDERS)
}

describe('KeysScreen', () => {
  it('groups keys by provider and only ever shows the masked value', async () => {
    setup()
    renderRoute('/keys')

    const gemini = await screen.findByRole('region', { name: 'Gemini' })
    const nvidia = screen.getByRole('region', { name: 'NVIDIA' })
    expect(within(gemini).getByText('Key 1')).toBeInTheDocument()
    expect(within(gemini).getByText('Key 2')).toBeInTheDocument()
    expect(within(nvidia).getByText('Key 3')).toBeInTheDocument()
    expect(within(gemini).getAllByText('AIza...wxyz')).toHaveLength(2)
  })

  it('pairs every status with an icon-bearing label, a cooldown and the auto-disable note', async () => {
    const soon = Math.floor(Date.now() / 1000) + 600
    setup([
      key(1, { status: 'invalid', enabled: false, auto_disabled: true, consecutive_failures: 3 }),
      key(2, { cooldowns: [{ model_id: '1', expires_at: soon, source: 'heuristic' }] }),
      key(3, { status: 'unknown', last_checked_at: null }),
      key(4, { status: 'rate_limited' }),
    ])
    renderRoute('/keys')

    expect(await screen.findByText('Invalid')).toBeInTheDocument()
    expect(screen.getByText('Disabled after 3 failed checks')).toBeInTheDocument()
    expect(screen.getByText('Cooling down')).toBeInTheDocument()
    expect(screen.getByText(/left$/)).toBeInTheDocument()
    expect(screen.getByText('Unchecked')).toBeInTheDocument()
    expect(screen.getByText('Never checked')).toBeInTheDocument()
    expect(screen.getByText('Rate limited')).toBeInTheDocument()
  })

  it('shows an empty state with an Add key button', async () => {
    setup([])
    renderRoute('/keys')

    expect(await screen.findByText(/No keys yet/)).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Add key' })).toHaveLength(2)
  })

  it('adds a key and sends the typed value once', async () => {
    const api = setup().on('POST', '/api/llm/keys', { status: 201, body: key(9) })
    const user = userEvent.setup()
    renderRoute('/keys')

    await user.click((await screen.findAllByRole('button', { name: 'Add key' }))[0])
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Label'), 'Personal')
    const field = within(dialog).getByLabelText('Key')
    expect(field).toHaveAttribute('type', 'password')
    await user.type(field, 'secret-value')
    await user.type(within(dialog).getByLabelText('Monthly request cap'), '1000')
    await user.click(within(dialog).getByRole('button', { name: 'Add key' }))

    await waitFor(() => expect(api.callsTo('POST', '/api/llm/keys')).toHaveLength(1))
    expect(api.callsTo('POST', '/api/llm/keys')[0].body).toEqual({
      provider: 'gemini',
      key: 'secret-value',
      label: 'Personal',
      monthly_request_cap: 1000,
      monthly_token_cap: null,
    })
    expect(await screen.findByText('Key added.')).toBeInTheDocument()
  })

  it('requires a key when adding', async () => {
    const api = setup()
    const user = userEvent.setup()
    renderRoute('/keys')

    await user.click((await screen.findAllByRole('button', { name: 'Add key' }))[0])
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Add key' }))

    expect(await within(dialog).findByText('Enter the API key.')).toBeInTheDocument()
    expect(api.callsTo('POST', '/api/llm/keys')).toHaveLength(0)
  })

  it('edits only what changed and keeps the stored key when the field is empty', async () => {
    const api = setup().on('PATCH', '/api/llm/keys/1', key(1, { label: 'Renamed' }))
    const user = userEvent.setup()
    renderRoute('/keys')

    await user.click(await screen.findByRole('button', { name: 'Actions for Key 1' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Edit' }))
    const dialog = await screen.findByRole('dialog')
    const label = within(dialog).getByLabelText('Label')
    await user.clear(label)
    await user.type(label, 'Renamed')
    await user.click(within(dialog).getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(api.callsTo('PATCH', '/api/llm/keys/1')).toHaveLength(1))
    expect(api.callsTo('PATCH', '/api/llm/keys/1')[0].body).toEqual({ label: 'Renamed' })
  })

  it('reveals a key only on request', async () => {
    const api = setup().on('POST', '/api/llm/keys/2/reveal', { key: 'AIzaSyFullSecretValue' })
    const user = userEvent.setup()
    renderRoute('/keys')

    await screen.findByText('Key 2')
    expect(screen.queryByDisplayValue('AIzaSyFullSecretValue')).not.toBeInTheDocument()
    expect(api.callsTo('POST', '/api/llm/keys/2/reveal')).toHaveLength(0)

    await user.click(screen.getByRole('button', { name: 'Actions for Key 2' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Reveal' }))

    expect(await screen.findByDisplayValue('AIzaSyFullSecretValue')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Copy' })).toBeInTheDocument()

    await user.click(screen.getAllByRole('button', { name: 'Close' })[0])
    await waitFor(() =>
      expect(screen.queryByDisplayValue('AIzaSyFullSecretValue')).not.toBeInTheDocument(),
    )
  })

  it('checks one key and updates its row', async () => {
    const api = setup([key(1, { status: 'unknown', last_checked_at: null })]).on(
      'POST',
      '/api/llm/keys/1/check',
      key(1, { status: 'invalid', last_health_error: 'Rejected by Gemini' }),
    )
    const user = userEvent.setup()
    renderRoute('/keys')

    await user.click(await screen.findByRole('button', { name: 'Actions for Key 1' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Check' }))

    await waitFor(() => expect(api.callsTo('POST', '/api/llm/keys/1/check')).toHaveLength(1))
    expect(await screen.findByText('Invalid')).toBeInTheDocument()
  })

  it('checks every key with Check all', async () => {
    const api = setup().on('POST', '/api/llm/keys/check-all', {
      keys: [key(1), key(2), key(3, { provider: 'nvidia', status: 'invalid' })],
    })
    const user = userEvent.setup()
    renderRoute('/keys')

    await user.click(await screen.findByRole('button', { name: 'Check all' }))

    await waitFor(() => expect(api.callsTo('POST', '/api/llm/keys/check-all')).toHaveLength(1))
    expect(await screen.findByText('Checked 3 keys: 2 healthy.')).toBeInTheDocument()
  })

  it('clears cooldowns only for a key that has one', async () => {
    const soon = Math.floor(Date.now() / 1000) + 600
    const api = setup([
      key(1, { cooldowns: [{ model_id: '*', expires_at: soon, source: 'credit' }] }),
      key(2),
    ]).on('DELETE', '/api/llm/keys/1/cooldowns', { cleared: 1 })
    const user = userEvent.setup()
    renderRoute('/keys')

    await user.click(await screen.findByRole('button', { name: 'Actions for Key 2' }))
    expect(await screen.findByRole('menuitem', { name: /Clear cooldowns/ })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
    await user.keyboard('{Escape}')

    await user.click(screen.getByRole('button', { name: 'Actions for Key 1' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Clear cooldowns' }))

    await waitFor(() => expect(api.callsTo('DELETE', '/api/llm/keys/1/cooldowns')).toHaveLength(1))
  })

  it('toggles one key and a whole provider group', async () => {
    const api = setup()
      .on('PATCH', '/api/llm/keys/1', key(1, { enabled: false }))
      .on('PATCH', '/api/llm/keys/2', key(2, { enabled: false }))
    const user = userEvent.setup()
    renderRoute('/keys')

    await user.click(await screen.findByRole('switch', { name: 'Disable Key 1' }))
    await waitFor(() => expect(api.callsTo('PATCH', '/api/llm/keys/1')).toHaveLength(1))
    expect(api.callsTo('PATCH', '/api/llm/keys/1')[0].body).toEqual({ enabled: false })

    await user.click(screen.getByRole('switch', { name: 'Disable all Gemini keys' }))
    await waitFor(() => expect(api.callsTo('PATCH', '/api/llm/keys/2')).toHaveLength(1))
  })

  it('deletes the selected keys after a confirmation that states the count', async () => {
    const api = setup()
      .on('DELETE', '/api/llm/keys/1', { status: 204 })
      .on('DELETE', '/api/llm/keys/2', { status: 204 })
    const user = userEvent.setup()
    renderRoute('/keys')

    await user.click(await screen.findByRole('checkbox', { name: 'Select Key 1' }))
    await user.click(screen.getByRole('checkbox', { name: 'Select Key 2' }))
    const bar = screen.getByRole('region', { name: 'Selected keys' })
    expect(within(bar).getByText('2 selected')).toBeInTheDocument()

    await user.click(within(bar).getByRole('button', { name: 'Delete' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/Delete 2 keys\?/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Delete 2 keys' }))

    await waitFor(() => {
      expect(api.callsTo('DELETE', '/api/llm/keys/1')).toHaveLength(1)
      expect(api.callsTo('DELETE', '/api/llm/keys/2')).toHaveLength(1)
    })
  })

  it('selects a whole provider group from its header checkbox', async () => {
    setup()
    const user = userEvent.setup()
    renderRoute('/keys')

    await user.click(await screen.findByRole('checkbox', { name: 'Select all Gemini keys' }))

    expect(screen.getByRole('checkbox', { name: 'Select Key 1' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Select Key 2' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Select Key 3' })).not.toBeChecked()
  })

  it('reports a failed load with a Retry', async () => {
    mockApi()
      .on('GET', '/api/llm/keys', {
        status: 500,
        body: { code: 'INTERNAL', message: 'Database is locked', details: {} },
      })
      .on('GET', '/api/llm/providers', PROVIDERS)
    renderRoute('/keys')

    expect(await screen.findByText('Could not load the keys')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})
