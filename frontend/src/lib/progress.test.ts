import { describe, expect, it } from 'vitest'

import { filterCount, progressSegments, STAGE_FILTERS, type StageFilter } from './progress'
import { ACTIVE_STAGES, type Stage } from './types'

const COUNTS: Partial<Record<Stage, number>> = {
  queued: 5,
  fetching_transcript: 2,
  transcript_ready: 3,
  generating_notes: 1,
  pending_review: 7,
  approved: 4,
  published: 10,
  failed: 2,
}
const TOTAL = 34

describe('summary strip filters', () => {
  it('counts In progress as the same stages it filters by', () => {
    expect(STAGE_FILTERS.in_progress).toEqual(ACTIVE_STAGES)
    expect(filterCount(COUNTS, 'in_progress', TOTAL)).toBe(6)
  })

  it('every filter count equals the sum of the stages it filters', () => {
    const filters = Object.keys(STAGE_FILTERS) as StageFilter[]
    for (const filter of filters) {
      const stages = STAGE_FILTERS[filter]
      const expected = stages ? stages.reduce((sum, s) => sum + (COUNTS[s] ?? 0), 0) : TOTAL
      expect(filterCount(COUNTS, filter, TOTAL), filter).toBe(expected)
    }
  })

  it('partitions the playlist: non-"all" filters sum to the total', () => {
    const filters = (Object.keys(STAGE_FILTERS) as StageFilter[]).filter((f) => f !== 'all')
    expect(filters.reduce((sum, f) => sum + filterCount(COUNTS, f, TOTAL), 0)).toBe(TOTAL)
  })
})

describe('progressSegments', () => {
  it('counts transcript_ready as in progress, not queued', () => {
    const byKey = Object.fromEntries(progressSegments(COUNTS).map((s) => [s.key, s.count]))
    expect(byKey.progress).toBe(6)
    expect(byKey.queued).toBe(5)
  })

  it('tolerates missing counts', () => {
    expect(progressSegments(undefined).every((s) => s.count === 0)).toBe(true)
  })
})
