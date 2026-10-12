import { describe, expect, it } from 'vitest'

import {
  attemptLabel,
  failureLabel,
  isRetriesExhausted,
  isRetryScheduled,
  stageToBadge,
  videoBadge,
  videoDetail,
  type RetryFields,
} from './stage-badge'
import { ALL_STAGES } from './types'

const NOW = Date.parse('2026-10-10T12:00:00Z')
const inMinutes = (m: number) => new Date(NOW + m * 60_000).toISOString()

function transient(extra: Partial<RetryFields> = {}): RetryFields {
  return { stage: 'failed', failure_kind: 'transient', retry_count: 3, ...extra }
}

describe('stageToBadge', () => {
  it('maps every backend stage to a tone, icon and label', () => {
    for (const stage of ALL_STAGES) {
      const spec = stageToBadge(stage)
      expect(spec.label, stage).toBeTruthy()
      expect(spec.icon, stage).toBeTruthy()
      expect(['neutral', 'progress', 'review', 'success', 'error', 'warn'], stage).toContain(
        spec.tone,
      )
    }
  })

  it('uses sentence case, never SCREAMING labels', () => {
    for (const stage of ALL_STAGES) {
      const { label } = stageToBadge(stage)
      expect(label).toBe(label.toLowerCase().replace(/^\w/, (c) => c.toUpperCase()))
    }
  })

  it('gives pending_review the attention colour (amber = needs you)', () => {
    expect(stageToBadge('pending_review').tone).toBe('review')
  })

  it('splits failed by failure kind', () => {
    expect(stageToBadge('failed', 'permanent').tone).toBe('error')
    expect(stageToBadge('failed', 'transient', { retryScheduled: true }).tone).toBe('warn')
  })

  it('defaults failed to the permanent/error badge when kind is missing', () => {
    expect(stageToBadge('failed').tone).toBe('error')
    expect(stageToBadge('failed', null).tone).toBe('error')
  })

  it('says Retrying only while a retry is actually scheduled', () => {
    expect(stageToBadge('failed', 'transient', { retryScheduled: true }).label).toBe('Retrying')
  })

  it('shows "Failed – temporary" (warn, refresh) once retries are exhausted', () => {
    const spec = stageToBadge('failed', 'transient', { retryScheduled: false })
    expect(spec.label).toBe('Failed – temporary')
    expect(spec.tone).toBe('warn')
    expect(spec.icon).toBe('refresh')
    expect(spec.inProgress).toBe(false)
    expect(stageToBadge('failed', 'transient').label).toBe('Failed – temporary')
  })

  it('keeps the real stage and adds a secondary badge for removed-upstream videos', () => {
    for (const stage of ALL_STAGES) {
      const plain = stageToBadge(stage, 'permanent')
      const spec = stageToBadge(stage, 'permanent', { removedUpstream: true })
      expect(spec.label, stage).toBe(plain.label)
      expect(spec.tone, stage).toBe(plain.tone)
      expect(spec.secondary).toMatchObject({
        label: 'No longer in playlist',
        icon: 'archive',
        tone: 'neutral',
      })
    }
    expect(stageToBadge('queued').secondary).toBeUndefined()
  })

  it('falls back safely on an unrecognised stage', () => {
    const spec = stageToBadge('not_a_real_stage' as never)
    expect(spec.label).toBe('Unknown')
    expect(spec.tone).toBe('neutral')
  })

  it('only reports in-progress for work that is actually moving', () => {
    expect(stageToBadge('fetching_transcript').inProgress).toBe(true)
    expect(stageToBadge('generating_notes').inProgress).toBe(true)
    expect(stageToBadge('queued').inProgress).toBe(false)
    expect(stageToBadge('pending_review').inProgress).toBe(false)
    expect(stageToBadge('published').inProgress).toBe(false)
  })
})

describe('retry state', () => {
  it('is scheduled when next_retry_at is set or auto_retry is true', () => {
    expect(isRetryScheduled(transient({ next_retry_at: inMinutes(2) }))).toBe(true)
    expect(isRetryScheduled(transient({ auto_retry: true }))).toBe(true)
  })

  it('is not scheduled when exhausted, auto_retry is false or nothing is set', () => {
    expect(
      isRetryScheduled(transient({ next_retry_at: inMinutes(2), retries_exhausted: true })),
    ).toBe(false)
    expect(isRetryScheduled(transient({ next_retry_at: inMinutes(2), auto_retry: false }))).toBe(
      false,
    )
    expect(isRetryScheduled(transient({ next_retry_at: null }))).toBe(false)
    expect(isRetryScheduled({ stage: 'failed', failure_kind: 'permanent', retry_count: 0 })).toBe(
      false,
    )
  })

  it('treats an unscheduled transient failure as exhausted', () => {
    expect(isRetriesExhausted(transient())).toBe(true)
    expect(isRetriesExhausted(transient({ next_retry_at: inMinutes(1) }))).toBe(false)
  })

  it('videoBadge folds retry state and removed-upstream into one badge', () => {
    expect(videoBadge({ ...transient({ next_retry_at: inMinutes(1) }) }).label).toBe('Retrying')
    expect(videoBadge({ ...transient(), removed_upstream: true }).secondary?.label).toBe(
      'No longer in playlist',
    )
  })
})

describe('failureLabel', () => {
  it('prefers the backend reason when present', () => {
    expect(failureLabel('rate_limited', 'YouTube throttled us')).toBe('YouTube throttled us')
  })

  it('never surfaces a raw snake_case code to the user', () => {
    const codes = [
      'video_private',
      'transcripts_disabled',
      'no_usable_language',
      'transcript_too_large',
      'output_malformed',
      'playlist_unavailable',
      'drive_error',
    ] as const

    for (const code of codes) {
      const label = failureLabel(code, null)
      expect(label, code).toBeTruthy()
      expect(label, code).not.toContain('_')
      expect(label, code).not.toBe(code)
    }
  })

  it('falls back to a generic label when nothing is known', () => {
    expect(failureLabel(null, null)).toBe('Failed')
  })
})

describe('videoDetail', () => {
  it('shows the reason for a permanent failure in the error tone', () => {
    expect(
      videoDetail({
        stage: 'failed',
        failure_type: 'transcripts_disabled',
        failure_reason: 'Transcripts are disabled by the uploader',
        failure_kind: 'permanent',
        retry_count: 0,
      }),
    ).toEqual({
      reason: 'Transcripts are disabled by the uploader',
      note: 'Not retried automatically - use Retry',
      tone: 'error',
      edited: false,
    })
  })

  it('shows a countdown and attempt N/M for a scheduled transient retry', () => {
    const detail = videoDetail(transient({ next_retry_at: inMinutes(2), max_auto_retries: 5 }), NOW)
    expect(detail.note).toBe('Retrying in 2m (attempt 3/5)')
    expect(detail.tone).toBe('warn')
  })

  it('does not fake the maximum when the API does not expose it', () => {
    const detail = videoDetail(transient({ next_retry_at: inMinutes(2) }), NOW)
    expect(detail.note).toBe('Retrying in 2m (attempt 3)')
    expect(attemptLabel(0)).toBe('attempt 1')
  })

  it('says retries are exhausted when nothing is scheduled', () => {
    const detail = videoDetail(transient({ next_retry_at: null }), NOW)
    expect(detail.note).toBe('Gave up after 3 automatic retries - use Retry')
    expect(
      videoDetail(transient({ next_retry_at: null, max_auto_retries: 5, retry_count: 5 })).note,
    ).toBe('Gave up after 5/5 automatic retries - use Retry')
    expect(detail.tone).toBe('warn')
  })

  it('marks an edited note', () => {
    expect(
      videoDetail({ stage: 'approved', failure_kind: null, retry_count: 0, edited: true }).edited,
    ).toBe(true)
  })

  it('has nothing to say for a healthy, unedited video', () => {
    expect(videoDetail({ stage: 'queued', failure_kind: null, retry_count: 0 })).toEqual({
      reason: null,
      note: null,
      tone: 'muted',
      edited: false,
    })
  })
})
