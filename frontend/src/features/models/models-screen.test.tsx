import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { formatPosition } from '../../lib/format'
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
    hidden: false,
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
    expect(await screen.findByRole('searchbox', { name: 'Search models' })).toBeInTheDocument()
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

  it('collapses and expands a provider group', async () => {
    setup([entry(1, { provider: 'gemini' }), entry(2, { provider: 'nvidia' })])
    const user = userEvent.setup()
    renderRoute('/models')

    const toggle = await screen.findByRole('button', { name: /gemini/i, expanded: true })
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    await user.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(rowNames()).toEqual(['Model 2'])
    await user.click(toggle)
    expect(rowNames()).toEqual(['Model 1', 'Model 2'])
  })

  it('selects and deselects all models of one provider', async () => {
    setup([
      entry(1, { provider: 'gemini' }),
      entry(2, { provider: 'gemini' }),
      entry(3, { provider: 'nvidia' }),
    ])
    const user = userEvent.setup()
    renderRoute('/models')

    await user.click(await screen.findByRole('button', { name: 'Deselect all gemini models' }))
    expect(screen.getByRole('switch', { name: 'Enable Model 1' })).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: 'Enable Model 2' })).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: 'Disable Model 3' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Select all gemini models' }))
    expect(screen.getByRole('switch', { name: 'Disable Model 1' })).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: 'Disable Model 2' })).toBeInTheDocument()
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
      entries: [entry(1), entry(3), entry(2, { enabled: false, chain_enabled: false })],
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
        { model_id: 3, priority: 2, enabled: true },
        { model_id: 2, priority: 3, enabled: false },
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

  it('re-enabling puts the model after the last enabled one', async () => {
    const off = { enabled: false, chain_enabled: false }
    setup([entry(1), entry(2), entry(3, off), entry(4, off)])
    const user = userEvent.setup()
    renderRoute('/models')

    await user.click(await screen.findByRole('switch', { name: 'Enable Model 4' }))
    expect(rowNames()).toEqual(['Model 1', 'Model 2', 'Model 4', 'Model 3'])
  })

  it('select all after deselect all moves only the changed models', async () => {
    setup([
      entry(1, { enabled: false, chain_enabled: false }),
      entry(2, { provider: 'nvidia' }),
      entry(3),
    ])
    const user = userEvent.setup()
    renderRoute('/models')

    await user.click(await screen.findByRole('button', { name: 'Select all gemini models' }))
    // Model 1 was the only change, so it goes after the last enabled model (3).
    expect(rowNames()).toEqual(['Model 2', 'Model 3', 'Model 1'])
  })

  it('labels the first enabled model Primary', async () => {
    setup([entry(1, { enabled: false, chain_enabled: false }), entry(2), entry(3)])
    renderRoute('/models')

    const second = (await screen.findByRole('textbox', { name: 'Rank of Model 2' })).closest('li')!
    const first = screen.getByRole('textbox', { name: 'Rank of Model 1' }).closest('li')!
    expect(within(second).getByText('Primary')).toBeInTheDocument()
    expect(within(first).queryByText('Primary')).not.toBeInTheDocument()
  })

  it('labels no row Primary when the first enabled model is hidden', async () => {
    setup([
      entry(1, { provider: 'openai', key_count: 1, enabled_key_count: 0, usable_key_count: 0 }),
      entry(2),
    ])
    renderRoute('/models')

    await screen.findAllByText('Model 2')
    expect(screen.queryByText('Primary')).not.toBeInTheDocument()
  })

  it('announces a toggle in the live region', async () => {
    setup()
    const user = userEvent.setup()
    renderRoute('/models')

    await user.click(await screen.findByRole('switch', { name: 'Disable Model 2' }))
    expect(screen.getByText('Model 2 disabled, moved to rank 3')).toBeInTheDocument()
  })

  describe('search', () => {
    const MIXED = () => [
      entry(1),
      entry(2, { provider: 'nvidia' }),
      entry(3),
    ]
    const searchBox = () => screen.getByRole('searchbox', { name: 'Search models' })

    it('filters by name, id and provider', async () => {
      setup(MIXED())
      const user = userEvent.setup()
      renderRoute('/models')

      await screen.findByRole('searchbox', { name: 'Search models' })
      await user.type(searchBox(), 'model-3')
      expect(rowNames()).toEqual(['Model 3'])
      await user.clear(searchBox())
      await user.type(searchBox(), 'NVIDIA')
      expect(rowNames()).toEqual(['Model 2'])
      await user.clear(searchBox())
      await user.type(searchBox(), 'model 1')
      expect(rowNames()).toEqual(['Model 1'])
    })

    it('hides groups with no matches and opens collapsed groups that match', async () => {
      setup(MIXED())
      const user = userEvent.setup()
      renderRoute('/models')

      await user.click(await screen.findByRole('button', { name: /gemini/i, expanded: true }))
      expect(rowNames()).toEqual(['Model 2'])

      await user.type(searchBox(), 'model-1')
      expect(rowNames()).toEqual(['Model 1'])
      expect(screen.queryByRole('region', { name: 'nvidia models' })).not.toBeInTheDocument()
    })

    it('restores the collapse layout after clearing a search', async () => {
      setup(MIXED())
      const user = userEvent.setup()
      renderRoute('/models')

      await user.click(await screen.findByRole('button', { name: /gemini/i, expanded: true }))
      await user.type(searchBox(), 'model-1')
      expect(rowNames()).toEqual(['Model 1'])
      await user.clear(searchBox())
      expect(rowNames()).toEqual(['Model 2'])
    })

    it('shows the no-match state with a Clear search button and keeps the collapse layout', async () => {
      setup(MIXED())
      const user = userEvent.setup()
      renderRoute('/models')

      await user.click(await screen.findByRole('button', { name: /gemini/i, expanded: true }))
      await user.type(searchBox(), 'zzz')
      expect(screen.getByText('No models match your search.')).toBeInTheDocument()

      await user.click(screen.getByRole('button', { name: 'Clear search' }))
      expect(searchBox()).toHaveValue('')
      expect(rowNames()).toEqual(['Model 2'])
    })

    it('keeps rank numbers at chain positions while searching', async () => {
      setup(MIXED())
      const user = userEvent.setup()
      renderRoute('/models')

      await screen.findByRole('searchbox', { name: 'Search models' })
      await user.type(searchBox(), 'model-3')
      expect(screen.getByRole('textbox', { name: 'Rank of Model 3' })).toHaveValue(
        formatPosition(3, 2),
      )
    })

    it('turns off drag and the rank input while searching', async () => {
      setup(MIXED())
      const user = userEvent.setup()
      renderRoute('/models')

      await screen.findByRole('searchbox', { name: 'Search models' })
      await user.type(searchBox(), 'model-3')
      const rank = screen.getByRole('textbox', { name: 'Rank of Model 3' })
      expect(rank).toHaveAttribute('readonly')
      expect(
        screen.getByRole('button', { name: 'Reordering Model 3 is unavailable while searching' }),
      ).toHaveAttribute('aria-disabled', 'true')

      await user.type(rank, '1{Enter}')
      expect(rowNames()).toEqual(['Model 3'])
      expect(screen.queryByRole('region', { name: 'Unsaved changes' })).not.toBeInTheDocument()

      await user.clear(searchBox())
      expect(screen.getByRole('textbox', { name: 'Rank of Model 3' })).not.toHaveAttribute(
        'readonly',
      )
      expect(screen.getByRole('button', { name: /^Reorder Model 3/ })).toHaveAttribute(
        'aria-disabled',
        'false',
      )
    })

    it('limits select all and deselect all to matches', async () => {
      setup([
        entry(1, { display_name: 'Alpha one' }),
        entry(2, { display_name: 'Alpha two' }),
        entry(3, { display_name: 'Beta' }),
      ])
      const user = userEvent.setup()
      renderRoute('/models')

      await screen.findAllByText('Beta')
      await user.type(searchBox(), 'alpha')
      await user.click(screen.getByRole('button', { name: 'Deselect 2 matching gemini models' }))
      await user.clear(searchBox())

      expect(screen.getByRole('switch', { name: 'Enable Alpha one' })).toBeInTheDocument()
      expect(screen.getByRole('switch', { name: 'Enable Alpha two' })).toBeInTheDocument()
      expect(screen.getByRole('switch', { name: 'Disable Beta' })).toBeInTheDocument()
    })

    it('shows N of M and the matching count labels', async () => {
      setup([entry(1), entry(2), entry(3, { provider: 'nvidia' })])
      const user = userEvent.setup()
      renderRoute('/models')

      await screen.findByRole('searchbox', { name: 'Search models' })
      await user.type(searchBox(), 'model-1')
      expect(screen.getByText('1 of 2')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Select 1 matching gemini models' })).toBeVisible()
      expect(screen.getByText('Select 1 matching')).toBeInTheDocument()
    })

    it('disables group headers while searching', async () => {
      setup(MIXED())
      const user = userEvent.setup()
      renderRoute('/models')

      await screen.findByRole('searchbox', { name: 'Search models' })
      await user.type(searchBox(), 'model')
      expect(screen.getByRole('button', { name: /gemini/i, expanded: true })).toBeDisabled()
    })

    it('Escape clears a non-empty search', async () => {
      setup(MIXED())
      const user = userEvent.setup()
      renderRoute('/models')

      await screen.findByRole('searchbox', { name: 'Search models' })
      await user.type(searchBox(), 'model-3{Escape}')
      expect(searchBox()).toHaveValue('')
      expect(rowNames()).toEqual(['Model 1', 'Model 3', 'Model 2'])
    })

    it('shows the result count and the reorder-off line', async () => {
      setup(MIXED())
      const user = userEvent.setup()
      renderRoute('/models')

      await screen.findByRole('searchbox', { name: 'Search models' })
      expect(screen.getByText('3 models')).toBeInTheDocument()
      await user.type(searchBox(), 'model-1')
      expect(
        screen.getByText('1 of 3 models. Reordering is off while searching.'),
      ).toBeInTheDocument()
    })

    it('shows no search row when no model is visible', async () => {
      setup([entry(1, { key_count: 1, enabled_key_count: 0, usable_key_count: 0 })])
      renderRoute('/models')

      expect(await screen.findByText(/No models to show/)).toBeInTheDocument()
      expect(screen.queryByRole('searchbox')).not.toBeInTheDocument()
    })

    it('does not mark the page changed and keeps stat cards and Serving order unfiltered', async () => {
      setup(MIXED())
      const user = userEvent.setup()
      renderRoute('/models')

      await screen.findByRole('searchbox', { name: 'Search models' })
      await user.type(searchBox(), 'model-1')
      expect(screen.queryByRole('region', { name: 'Unsaved changes' })).not.toBeInTheDocument()
      const servingOrder = screen.getByText('Serving order').parentElement
      expect(servingOrder).toHaveTextContent('Model 1')
      expect(servingOrder).toHaveTextContent('Model 2')
      expect(servingOrder).toHaveTextContent('Model 3')
    })
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

  describe('bulk hide and delete', () => {
    it('hides the ticked models in one request', async () => {
      const api = setup().on('POST', '/api/llm/models/bulk-hide', { updated: 2 })
      const user = userEvent.setup()
      renderRoute('/models')

      await user.click(await screen.findByRole('checkbox', { name: 'Select Model 1' }))
      await user.click(screen.getByRole('checkbox', { name: 'Select Model 3' }))
      const bar = screen.getByRole('region', { name: 'Selected models' })
      expect(within(bar).getByText('2 selected')).toBeInTheDocument()
      await user.click(within(bar).getByRole('button', { name: 'Hide' }))

      await waitFor(() => expect(api.callsTo('POST', '/api/llm/models/bulk-hide')).toHaveLength(1))
      expect(api.callsTo('POST', '/api/llm/models/bulk-hide')[0].body).toEqual({
        ids: [1, 3],
        hidden: true,
      })
      await waitFor(() =>
        expect(screen.queryByRole('region', { name: 'Selected models' })).not.toBeInTheDocument(),
      )
    })

    it('deletes the ticked models after confirmation, using the group checkbox', async () => {
      const api = setup().on('POST', '/api/llm/models/bulk-delete', { deleted: 3 })
      const user = userEvent.setup()
      renderRoute('/models')

      await user.click(await screen.findByRole('checkbox', { name: 'Tick all gemini models' }))
      await user.click(
        within(screen.getByRole('region', { name: 'Selected models' })).getByRole('button', {
          name: 'Delete',
        }),
      )
      const dialog = await screen.findByRole('dialog')
      expect(api.callsTo('POST', '/api/llm/models/bulk-delete')).toHaveLength(0)
      await user.click(within(dialog).getByRole('button', { name: 'Delete 3 models' }))

      await waitFor(() => expect(api.callsTo('POST', '/api/llm/models/bulk-delete')).toHaveLength(1))
      expect(api.callsTo('POST', '/api/llm/models/bulk-delete')[0].body).toEqual({
        ids: [1, 2, 3],
      })
    })

    it('shows no gap in the ranks once a model is hidden', async () => {
      setup([entry(1), entry(3), entry(2, { hidden: true, enabled: false, chain_enabled: false })])
      renderRoute('/models')

      expect(await screen.findByRole('textbox', { name: 'Rank of Model 1' })).toHaveValue(
        formatPosition(1, 2),
      )
      expect(screen.getByRole('textbox', { name: 'Rank of Model 3' })).toHaveValue(
        formatPosition(2, 2),
      )
    })

    it('lists hidden models only with Show hidden and unhides them', async () => {
      const api = setup([entry(1), entry(2, { hidden: true, enabled: false, chain_enabled: false })])
        .on('POST', '/api/llm/models/bulk-hide', { updated: 1 })
      const user = userEvent.setup()
      renderRoute('/models')

      await screen.findByRole('textbox', { name: 'Rank of Model 1' })
      expect(screen.queryByRole('textbox', { name: 'Rank of Model 2' })).not.toBeInTheDocument()

      await user.click(screen.getByRole('checkbox', { name: 'Show hidden (1)' }))
      expect(await screen.findByRole('textbox', { name: 'Rank of Model 2' })).toBeInTheDocument()
      expect(screen.getByRole('switch', { name: 'Enable Model 2' })).toBeDisabled()

      await user.click(screen.getByRole('checkbox', { name: 'Select Model 2' }))
      await user.click(
        within(screen.getByRole('region', { name: 'Selected models' })).getByRole('button', {
          name: 'Unhide',
        }),
      )

      await waitFor(() => expect(api.callsTo('POST', '/api/llm/models/bulk-hide')).toHaveLength(1))
      expect(api.callsTo('POST', '/api/llm/models/bulk-hide')[0].body).toEqual({
        ids: [2],
        hidden: false,
      })
    })
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
