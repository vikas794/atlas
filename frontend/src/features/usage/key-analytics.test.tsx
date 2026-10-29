import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { KeyAnalyticsResponse } from '../../lib/types'
import { mockApi, renderRoute } from '../../test/harness'

const ANALYTICS: KeyAnalyticsResponse = {
  range: '7d',
  total_requests: 3,
  total_errors: 1,
  total_cost_usd: 2,
  by_key: [
    {
      key_id: 1,
      label: 'Personal',
      provider: 'gemini',
      requests: 2,
      successes: 2,
      errors: 0,
      success_rate: 100,
      avg_latency_ms: 200,
      input_tokens: 30,
      output_tokens: 10,
      cost_usd: 2,
    },
    {
      key_id: 7,
      label: null,
      provider: 'gemini',
      requests: 1,
      successes: 0,
      errors: 1,
      success_rate: 0,
      avg_latency_ms: 50,
      input_tokens: 0,
      output_tokens: 0,
      cost_usd: 0,
    },
  ],
  by_model: [
    {
      provider: 'gemini',
      model: 'gem-1',
      requests: 3,
      successes: 2,
      errors: 1,
      success_rate: 66.7,
      input_tokens: 30,
      output_tokens: 10,
      total_tokens: 40,
      cost_usd: 2,
      avg_cost_usd: 0.6667,
      cost_share: 100,
    },
  ],
  errors: [
    {
      category: 'rejected',
      count: 1,
      details: [
        {
          provider: 'gemini',
          model: 'gem-9',
          operation: 'generate_notes',
          key_label: 'Personal',
          count: 1,
          last_at: '2026-01-01T00:00:00+00:00',
          last_message: 'API key rejected',
        },
      ],
    },
  ],
}

describe('KeyAnalytics', () => {
  it('shows success and errors per key, and cost per model', async () => {
    mockApi()
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
      .on('GET', '/api/usage/keys', ANALYTICS)
    renderRoute('/settings')

    const section = await screen.findByRole('region', { name: 'API key analytics' })
    expect(await within(section).findByText('Personal')).toBeInTheDocument()
    expect(within(section).getByText('Key #7')).toBeInTheDocument()
    expect(within(section).getByText('Cost per model')).toBeInTheDocument()
    expect(within(section).getByText('gem-1')).toBeInTheDocument()
    await userEvent.click(within(section).getByRole('button', { name: /rejected/ }))
    expect(within(section).getByText('gem-9')).toBeInTheDocument()
    expect(within(section).getByText('API key rejected')).toBeInTheDocument()
  })
})
