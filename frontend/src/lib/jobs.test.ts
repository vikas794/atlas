import { describe, expect, it } from 'vitest'

import { describePublish, hasPublishCounts, publishPhase, regeneratePhase } from './jobs'

const watch = { startedAt: 1000 }

describe('publishPhase', () => {
  it('is idle without a watch', () => {
    expect(publishPhase({ status: 'ready' }, 2000, null)).toBe('idle')
  })

  it('stays running while the playlist is publishing or the data is stale', () => {
    expect(publishPhase({ status: 'publishing' }, 2000, watch)).toBe('running')
    expect(publishPhase({ status: 'ready' }, 500, watch)).toBe('running')
    expect(publishPhase(undefined, 2000, watch)).toBe('running')
  })

  it('finishes as succeeded or failed once fresh data stops publishing', () => {
    expect(publishPhase({ status: 'ready' }, 2000, watch)).toBe('succeeded')
    expect(publishPhase({ status: 'publish_failed' }, 2000, watch)).toBe('failed')
  })
})

describe('regeneratePhase', () => {
  it('runs while the video is generating and ends when it settles', () => {
    expect(regeneratePhase({ stage: 'generating_notes' }, 2000, watch)).toBe('running')
    expect(regeneratePhase({ stage: 'pending_review' }, 2000, watch)).toBe('succeeded')
    expect(regeneratePhase({ stage: 'failed' }, 2000, watch)).toBe('failed')
    expect(regeneratePhase({ stage: 'pending_review' }, 500, watch)).toBe('running')
  })
})

describe('describePublish', () => {
  it('reports real counts', () => {
    expect(describePublish({ created: 3, updated: 1 })).toBe(
      'Published 3 new Docs, updated 1 Doc in Drive.',
    )
    expect(describePublish({ created: 0, updated: 2 })).toBe('Updated 2 Docs in Drive.')
  })

  it('never says "Published 0 new Docs"', () => {
    expect(describePublish({ created: 0, updated: 0, skipped: 5 })).toBe('Nothing new to publish.')
    expect(describePublish(null, 0)).toBe('Nothing new to publish.')
  })

  it('falls back to the published-count delta for async publishes', () => {
    expect(describePublish(null, 4)).toBe('Published 4 videos to Drive.')
  })

  it('tells sync (200) responses from async (202) ones', () => {
    expect(hasPublishCounts({ created: 1, updated: 0 })).toBe(true)
    expect(hasPublishCounts({})).toBe(false)
    expect(hasPublishCounts(undefined)).toBe(false)
  })
})
