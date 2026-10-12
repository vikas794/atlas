import { describe, expect, it } from 'vitest'

import { FAST_POLL_MS, pollInterval, SLOW_POLL_MS } from './polling'
import type { RetryFields } from './stage-badge'
import type { Stage } from './types'

const video = (stage: Stage, extra: Partial<RetryFields> = {}): RetryFields => ({
  stage,
  failure_kind: null,
  retry_count: 0,
  ...extra,
})

describe('pollInterval', () => {
  it('is slow when everything is settled', () => {
    expect(
      pollInterval({
        videos: [video('pending_review'), video('approved'), video('published')],
        playlist: { status: 'ready', stage_counts: { pending_review: 1 } as never },
      }),
    ).toBe(SLOW_POLL_MS)
    expect(pollInterval({})).toBe(SLOW_POLL_MS)
  })

  it.each(['queued', 'fetching_transcript', 'transcript_ready', 'generating_notes'] as Stage[])(
    'is fast while a video is %s',
    (stage) => {
      expect(pollInterval({ videos: [video(stage)] })).toBe(FAST_POLL_MS)
    },
  )

  it('is fast while a transient retry is scheduled', () => {
    const retrying = video('failed', {
      failure_kind: 'transient',
      next_retry_at: new Date(Date.now() + 60_000).toISOString(),
    })
    expect(pollInterval({ videos: [retrying] })).toBe(FAST_POLL_MS)
  })

  it('stays slow for a failure with retries exhausted', () => {
    expect(pollInterval({ videos: [video('failed', { failure_kind: 'transient' })] })).toBe(SLOW_POLL_MS)
    expect(pollInterval({ videos: [video('failed', { failure_kind: 'permanent' })] })).toBe(SLOW_POLL_MS)
  })

  it('is fast while the playlist is publishing', () => {
    expect(pollInterval({ playlist: { status: 'publishing', stage_counts: {} as never } })).toBe(FAST_POLL_MS)
  })

  it('is fast when playlist counts show queued or active work', () => {
    expect(
      pollInterval({ playlist: { status: 'ready', stage_counts: { transcript_ready: 2 } as never } }),
    ).toBe(FAST_POLL_MS)
  })

  it('is fast while an async regenerate or publish is running', () => {
    expect(pollInterval({ asyncRunning: true })).toBe(FAST_POLL_MS)
  })
})
