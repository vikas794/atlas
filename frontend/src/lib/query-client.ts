import { QueryClient } from '@tanstack/react-query'

import { ApiError } from './api'

const MAX_RETRIES = 2

/**
 * Retry network and 5xx failures, but never 4xx. A 404 (for example "no notes
 * generated yet") is an answer, not a glitch, so retrying it three times only
 * delays the screen that has to explain it.
 */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false
  return failureCount < MAX_RETRIES
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: shouldRetry, refetchOnWindowFocus: false } },
  })
}
