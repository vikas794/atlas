import { describe, expect, it } from 'vitest'

import { describePublish, publishPhase, regeneratePhase } from './jobs'

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
  it('reports how many videos the finished publish added', () => {
    expect(describePublish(4)).toBe('Published 4 videos to Drive.')
    expect(describePublish(1)).toBe('Published 1 video to Drive.')
  })

  it('never says "Published 0"', () => {
    expect(describePublish(0)).toBe('Publish finished. Nothing new to publish.')
  })
})
