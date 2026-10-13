import { describe, expect, it } from 'vitest'

import { findNextNeedingReview } from './review-nav'
import type { Stage } from './types'

const list = (stages: Stage[]) => stages.map((stage, i) => ({ id: i + 1, stage }))

describe('findNextNeedingReview', () => {
  it('prefers the next one after the current video', () => {
    expect(findNextNeedingReview(list(['pending_review', 'approved', 'pending_review']), 1)).toBe(3)
  })

  it('wraps around to an earlier one', () => {
    expect(findNextNeedingReview(list(['pending_review', 'approved', 'approved']), 3)).toBe(1)
  })

  it('skips the current video and returns null when nothing else needs review', () => {
    expect(findNextNeedingReview(list(['pending_review', 'approved']), 1)).toBeNull()
    expect(findNextNeedingReview([], 1)).toBeNull()
  })

  it('works for videos beyond the first 250', () => {
    const videos = list(
      Array.from({ length: 400 }, (_, i) => (i === 399 ? 'pending_review' : 'approved')) as Stage[],
    )
    expect(findNextNeedingReview(videos, 300)).toBe(400)
  })
})
