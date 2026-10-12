import { describe, expect, it } from 'vitest'

import { ApiError } from './api'
import { shouldRetry } from './query-client'

describe('shouldRetry', () => {
  it('never retries a 4xx such as "notes not generated yet" (404)', () => {
    expect(shouldRetry(0, new ApiError('Not found', 'not_found', {}, 404))).toBe(false)
    expect(shouldRetry(0, new ApiError('Bad', 'validation_error', {}, 422))).toBe(false)
  })

  it('retries network errors and 5xx a limited number of times', () => {
    expect(shouldRetry(0, new ApiError('down', 'network_error', {}, 0))).toBe(true)
    expect(shouldRetry(1, new ApiError('boom', 'server_error', {}, 500))).toBe(true)
    expect(shouldRetry(2, new ApiError('boom', 'server_error', {}, 500))).toBe(false)
  })
})
