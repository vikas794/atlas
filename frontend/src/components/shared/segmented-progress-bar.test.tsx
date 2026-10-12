import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { StageCounts } from '../../lib/progress'
import { SegmentedProgressBar } from './segmented-progress-bar'

const counts = {
  queued: 0,
  fetching_transcript: 0,
  transcript_ready: 0,
  generating_notes: 0,
  pending_review: 1,
  approved: 1,
  published: 2,
  failed: 3,
} as StageCounts

describe('SegmentedProgressBar', () => {
  it('derives its total from the stage counts when video_count is stale or zero', () => {
    render(<SegmentedProgressBar counts={counts} total={0} />)

    expect(screen.queryByText('0 videos')).not.toBeInTheDocument()
    expect(screen.getByText(/2 of 7 published/)).toBeInTheDocument()
  })

  it('shows the empty bar only when there are genuinely no videos', () => {
    render(
      <SegmentedProgressBar
        counts={{ ...counts, pending_review: 0, approved: 0, published: 0, failed: 0 }}
        total={0}
      />,
    )

    expect(screen.getByText('0 videos')).toBeInTheDocument()
  })

  it('exposes progress to assistive tech as a labelled progressbar', () => {
    render(<SegmentedProgressBar counts={counts} total={7} />)

    const bar = screen.getByRole('progressbar', { name: 'Playlist progress' })
    expect(bar).toHaveAttribute('aria-valuenow', '2')
    expect(bar).toHaveAttribute('aria-valuemax', '7')
    expect(bar).toHaveAttribute(
      'aria-valuetext',
      '2 of 7 published (2 published, 1 approved, 1 needs review, 3 failed)',
    )
  })
})
