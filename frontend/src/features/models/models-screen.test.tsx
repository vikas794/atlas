import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { FallbackEntry } from '../../lib/types'
import { mockApi, renderRoute } from '../../test/harness'

function entry(id: number, overrides: Partial<FallbackEntry> = {}): FallbackEntry {
  return {
    id,
    provider: 'gemini',
    model_id: `model-${id}`,
    display_name: `Model ${id}`,
    litellm_model: `gemini/model-${id}`,
    api_base: null,
    input_token_limit: 128_000,
    request_timeout_seconds: null,
    rpm_limit: null,
    rpd_limit: null,
    tpm_limit: null,
    tpd_limit: null,
    enabled: true,
    source: 'user',
    priority: id,
    chain_enabled: true,
    key_count: 2,
    enabled_key_count: 2,
    usable_key_count: 2,
    blocked: null,
    ...overrides,
  }
}

const PROVIDERS = {
  providers: [
    { name: 'gemini', label: 'Gemini', default_api_base: null, key_count: 1, usable_key_count: 1 },
    {
      name: 'nvidia',
      label: 'NVIDIA',
      default_api_base: 'https://integrate.api.nvidia.com/v1',
      key_count: 0,
      usable_key_count: 0,
    },
  ],
}

function setup(entries: FallbackEntry[] = [entry(1), entry(2), entry(3)]) {
  return mockApi()
    .on('GET', '/api/llm/fallback', { entries })
    .on('GET', '/api/llm/fallback/rate-limit-usage', { rows: [] })
    .on('GET', '/api/llm/providers', PROVIDERS)
    .on('GET', '/api/llm/providers/gemini/available-models', {
      provider: 'gemini',
      models: ['gemini-3-flash', 'gemini-3-pro'],
    })
}

function rowNames(): string[] {
  return screen
    .getAllByRole('textbox', { name: /^Rank of / })
    .map((input) => input.getAttribute('aria-label')!.replace('Rank of ', ''))
}

describe('ModelsScreen', () => {
  it('lists the chain in order with provider, context, quota and key counts', async () => {
    setup([
      entry(1, { provider: 'nvidia', key_count: 0, usable_key_count: 0, blocked: 'no_keys' }),
      entry(2, { input_token_limit: 1_000_000 }),
    ])
    renderRoute('/models')

    expect(await screen.findByRole('heading', { name: 'Models' })).toBeInTheDocument()
    expect(await screen.findByText('Model 1')).toBeInTheDocument()
    expect(rowNames()).toEqual(['Model 1', 'Model 2'])
    expect(screen.getByRole('link', { name: 'No keys' })).toHaveAttribute('href', '/keys')
    expect(screen.getByText('1M')).toBeInTheDocument()
    expect(screen.getByText('2 of 2')).toBeInTheDocument()
  })

  it('groups models by provider and hides providers without an enabled key', async () => {
    setup([
      entry(1, { provider: 'gemini' }),
      entry(2, { provider: 'nvidia' }),
      entry(3, { provider: 'gemini' }),
      entry(4, { provider: 'openai', key_count: 1, enabled_key_count: 0, usable_key_count: 0 }),
    ])
    renderRoute('/models')

    await screen.findAllByText('Model 1')
    expect(screen.getByRole('region', { name: 'gemini models' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'nvidia models' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'openai models' })).not.toBeInTheDocument()
    expect(screen.queryAllByText('Model 4')).toHaveLength(0)
    // Gemini's two models sit together even though nvidia is ranked between them.
    expect(rowNames()).toEqual(['Model 1', 'Model 3', 'Model 2'])
  })

  it('shows an empty state with an Add model button', async () => {
    setup([])
    renderRoute('/models')

    expect(
      await screen.findByText('No models yet. Add one to start generating notes.'),
    ).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Add model' })).toHaveLength(2)
  })

  it('stages an enable toggle and saves it with dense priorities', async () => {
    const api = setup().on('PUT', '/api/llm/fallback', {
      entries: [entry(1), entry(2, { enabled: false, chain_enabled: false }), entry(3)],
    })
    const user = userEvent.setup()
    renderRoute('/models')

    expect(screen.queryByRole('region', { name: 'Unsaved changes' })).not.toBeInTheDocument()
    await user.click(await screen.findByRole('switch', { name: 'Disable Model 2' }))
    expect(screen.getByRole('region', { name: 'Unsaved changes' })).toBeInTheDocument()
    expect(api.callsTo('PUT', '/api/llm/fallback')).toHaveLength(0)

    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(api.callsTo('PUT', '/api/llm/fallback')).toHaveLength(1))
    expect(api.callsTo('PUT', '/api/llm/fallback')[0].body).toEqual({
      entries: [
        { model_id: 1, priority: 1, enabled: true },
        { model_id: 2, priority: 2, enabled: false },
        { model_id: 3, priority: 3, enabled: true },
      ],
    })
    expect(await screen.findByText('Model chain saved.')).toBeInTheDocument()
    await waitFor(() =>
      expect(screen.queryByRole('region', { name: 'Unsaved changes' })).not.toBeInTheDocument(),
    )
  })

  it('moves a model by typing its new rank, and Discard restores the saved order', async () => {
    setup()
    const user = userEvent.setup()
    renderRoute('/models')

    const rank = await screen.findByRole('textbox', { name: 'Rank of Model 3' })
    await user.clear(rank)
    await user.type(rank, '1{Enter}')

    expect(rowNames()).toEqual(['Model 3', 'Model 1', 'Model 2'])
    expect(screen.getByRole('region', { name: 'Unsaved changes' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Discard' }))
    expect(rowNames()).toEqual(['Model 1', 'Model 2', 'Model 3'])
    expect(screen.queryByRole('region', { name: 'Unsaved changes' })).not.toBeInTheDocument()
  })

  it('exposes a keyboard-operable drag handle per row', async () => {
    setup()
    renderRoute('/models')

    const handle = await screen.findByRole('button', { name: /^Reorder Model 1/ })
    expect(handle).toHaveAttribute('aria-roledescription', 'sortable')
  })

  it('adds a model to the end of the chain', async () => {
    const api = setup().on('POST', '/api/llm/models', {
      status: 201,
      body: { ...entry(4), model_id: 'gemini-3-pro' },
    })
    const user = userEvent.setup()
    renderRoute('/models')

    await user.click((await screen.findAllByRole('button', { name: 'Add model' }))[0])
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Model id'), 'gemini-3-pro')
    await user.type(within(dialog).getByLabelText('Requests per minute'), '15')
    await user.click(within(dialog).getByRole('button', { name: 'Add model' }))

    await waitFor(() => expect(api.callsTo('POST', '/api/llm/models')).toHaveLength(1))
    expect(api.callsTo('POST', '/api/llm/models')[0].body).toMatchObject({
      provider: 'gemini',
      model_id: 'gemini-3-pro',
      rpm_limit: 15,
    })
    expect(await screen.findByText('Model added to the end of the chain.')).toBeInTheDocument()
  })

  it('refuses an add without a model id and a limit that is not a number', async () => {
    const api = setup()
    const user = userEvent.setup()
    renderRoute('/models')

    await user.click((await screen.findAllByRole('button', { name: 'Add model' }))[0])
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Requests per day'), 'lots')
    await user.click(within(dialog).getByRole('button', { name: 'Add model' }))

    expect(await within(dialog).findByText('Enter a model id.')).toBeInTheDocument()
    expect(within(dialog).getByText('Enter a number above 0.')).toBeInTheDocument()
    expect(api.callsTo('POST', '/api/llm/models')).toHaveLength(0)
  })

  it('pings a model from the row menu and reports the latency', async () => {
    const api = setup().on('POST', '/api/llm/models/2/test', {
      success: true,
      latency_ms: 420,
      error: null,
    })
    const user = userEvent.setup()
    renderRoute('/models')

    await user.click(await screen.findByRole('button', { name: 'Actions for Model 2' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Test' }))

    await waitFor(() => expect(api.callsTo('POST', '/api/llm/models/2/test')).toHaveLength(1))
    expect(await screen.findByText('Model 2 answered in 420 ms.')).toBeInTheDocument()
  })

  it('shows the plain-text error of a failed test', async () => {
    setup().on('POST', '/api/llm/models/1/test', {
      success: false,
      latency_ms: 80,
      error: 'Gemini rejected the API key',
    })
    const user = userEvent.setup()
    renderRoute('/models')

    await user.click(await screen.findByRole('button', { name: 'Actions for Model 1' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Test' }))

    expect(await screen.findByText('Test failed: Gemini rejected the API key')).toBeInTheDocument()
  })

  it('deletes a model after confirmation', async () => {
    const api = setup().on('DELETE', '/api/llm/models/3', { status: 204 })
    const user = userEvent.setup()
    renderRoute('/models')

    await user.click(await screen.findByRole('button', { name: 'Actions for Model 3' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Delete' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Delete model' }))

    await waitFor(() => expect(api.callsTo('DELETE', '/api/llm/models/3')).toHaveLength(1))
  })

  it('reports a failed load with a Retry', async () => {
    mockApi()
      .on('GET', '/api/llm/fallback', {
        status: 500,
        body: { code: 'INTERNAL', message: 'Database is locked', details: {} },
      })
      .on('GET', '/api/llm/fallback/rate-limit-usage', { rows: [] })
    renderRoute('/models')

    expect(await screen.findByText('Could not load the models')).toBeInTheDocument()
    expect(screen.getByText('Database is locked')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})
