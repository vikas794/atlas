import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { SettingField, SettingsResponse } from '../../lib/types'
import { mockApi, renderRoute } from '../../test/harness'

function field(
  overrides: Partial<SettingField> & Pick<SettingField, 'key' | 'label'>,
): SettingField {
  return {
    group: 'pipeline',
    help: '',
    kind: 'int',
    unit: '',
    choices: [],
    minimum: 0,
    apply: 'live',
    editable: true,
    value: 0,
    default: 0,
    source: 'default',
    locked: false,
    ...overrides,
  }
}

function settings(overrides: Partial<SettingsResponse> = {}): SettingsResponse {
  return {
    groups: [
      { id: 'pipeline', label: 'Pipeline' },
      { id: 'timeouts', label: 'Timeouts and pacing' },
    ],
    fields: [
      field({
        key: 'max_concurrent_videos',
        label: 'Videos processed at once',
        value: 2,
        default: 2,
      }),
      field({
        key: 'worker_poll_interval_seconds',
        label: 'Worker poll interval',
        kind: 'float',
        unit: 's',
        value: 2,
        default: 2,
      }),
      field({
        key: 'notes_request_timeout_seconds',
        group: 'timeouts',
        label: 'LLM request timeout',
        kind: 'float',
        unit: 's',
        value: 300,
        default: 300,
      }),
    ],
    restart_required: false,
    ...overrides,
  }
}

function setup(response: SettingsResponse = settings()) {
  return mockApi()
    .on('GET', '/api/settings', response)
    .on('GET', '/api/drive/status', {
      configured: false,
      state: 'credentials_missing',
      message: '',
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

describe('AppConfigPanel', () => {
  it('switches to serial by saving one video at a time', async () => {
    const api = setup().on('PUT', '/api/settings', settings())
    const user = userEvent.setup()
    renderRoute('/settings')

    await user.click(await screen.findByRole('radio', { name: /Serial/ }))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(api.callsTo('PUT', '/api/settings')).toHaveLength(1))
    expect(api.callsTo('PUT', '/api/settings')[0].body).toEqual({
      values: { max_concurrent_videos: 1 },
    })
    expect(await screen.findByText('Settings saved and applied.')).toBeInTheDocument()
  })

  it('sends only the changed fields, as numbers', async () => {
    const api = setup().on('PUT', '/api/settings', settings())
    const user = userEvent.setup()
    renderRoute('/settings')

    const poll = await screen.findByLabelText(/Worker poll interval/)
    await user.clear(poll)
    await user.type(poll, '0.5')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(api.callsTo('PUT', '/api/settings')).toHaveLength(1))
    expect(api.callsTo('PUT', '/api/settings')[0].body).toEqual({
      values: { worker_poll_interval_seconds: 0.5 },
    })
  })

  it('keeps Save disabled until something changes', async () => {
    setup()
    renderRoute('/settings')

    expect(await screen.findByRole('button', { name: 'Save changes' })).toBeDisabled()
  })

  it('shows the server error next to the field that caused it', async () => {
    setup().on('PUT', '/api/settings', {
      status: 422,
      body: {
        code: 'VALIDATION_ERROR',
        message: 'Settings were not saved',
        details: {
          fields: { worker_poll_interval_seconds: 'must be greater than 0' },
          general: [],
        },
      },
    })
    const user = userEvent.setup()
    renderRoute('/settings')

    const poll = await screen.findByLabelText(/Worker poll interval/)
    await user.clear(poll)
    await user.type(poll, '0')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    expect(await screen.findByText('must be greater than 0')).toBeInTheDocument()
  })

  it('locks a setting that an environment variable decides', async () => {
    setup(
      settings({
        fields: [
          field({
            key: 'max_concurrent_videos',
            label: 'Videos processed at once',
            value: 4,
            source: 'env',
            locked: true,
          }),
        ],
      }),
    )
    renderRoute('/settings')

    expect(await screen.findByRole('radio', { name: /Serial/ })).toBeDisabled()
    expect(screen.getByText('Set by an environment variable')).toBeInTheDocument()
  })

  it('opens a group on demand and flags restart-only settings', async () => {
    setup(
      settings({
        restart_required: true,
        groups: [{ id: 'server', label: 'Server and logging' }],
        fields: [
          field({ key: 'port', group: 'server', label: 'Port', value: 8000, apply: 'restart' }),
        ],
      }),
    )
    const user = userEvent.setup()
    renderRoute('/settings')

    expect(await screen.findByText(/Restart required/)).toBeInTheDocument()
    const toggle = screen.getByRole('button', { name: 'Server and logging' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await user.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Needs restart')).toBeVisible()
  })

  it('resets saved changes after confirmation', async () => {
    const saved = settings({
      fields: [
        field({
          key: 'max_concurrent_videos',
          label: 'Videos processed at once',
          value: 1,
          default: 2,
          source: 'db',
        }),
      ],
    })
    const api = setup(saved).on('DELETE', '/api/settings', settings())
    const user = userEvent.setup()
    renderRoute('/settings')

    await user.click(await screen.findByRole('button', { name: 'Reset to defaults' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Reset to defaults' }))

    await waitFor(() => expect(api.callsTo('DELETE', '/api/settings')).toHaveLength(1))
  })

  it('closing the whole panel also closes every group inside it', async () => {
    setup()
    const user = userEvent.setup()
    renderRoute('/settings')

    const panel = await screen.findByRole('button', { name: /Application configuration/ })
    const group = await screen.findByRole('button', { name: 'Pipeline' })
    expect(panel).toHaveAttribute('aria-expanded', 'true')
    expect(group).toHaveAttribute('aria-expanded', 'true')

    await user.click(panel)
    expect(panel).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('button', { name: 'Save changes' })).not.toBeInTheDocument()

    await user.click(panel)
    expect(screen.getByRole('button', { name: 'Pipeline' })).toHaveAttribute(
      'aria-expanded',
      'false',
    )
  })
  it('picks the AI model from a dropdown and saves only that field', async () => {
    const notes = settings({
      groups: [{ id: 'notes', label: 'Notes generation' }],
      fields: [
        field({
          key: 'notes_provider',
          group: 'notes',
          label: 'Notes provider',
          kind: 'text',
          choices: ['gemini', 'openrouter', 'omniroute'],
          value: 'gemini',
          default: 'gemini',
        }),
        field({
          key: 'gemini_model',
          group: 'notes',
          label: 'Gemini model',
          kind: 'text',
          value: 'gemini-3.5-flash',
          default: 'gemini-3.5-flash',
        }),
      ],
    })
    const api = setup(notes)
      .on('GET', '/api/settings/models', {
        provider: 'gemini',
        models: ['gemini-3.8-flash', 'gemini-3.6-flash'],
      })
      .on('PUT', '/api/settings', notes)
    const user = userEvent.setup()
    renderRoute('/settings')

    await user.click(await screen.findByRole('button', { name: 'Notes generation' }))
    const model = await screen.findByLabelText('AI model')
    await screen.findByRole('option', { name: 'gemini-3.8-flash' })
    await user.selectOptions(model, 'gemini-3.8-flash')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(api.callsTo('PUT', '/api/settings')).toHaveLength(1))
    expect(api.callsTo('PUT', '/api/settings')[0].body).toEqual({
      values: { gemini_model: 'gemini-3.8-flash' },
    })
  })
})
