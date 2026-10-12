import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { StageDot, StatusBadge } from './status-badge'
import { stageToBadge } from '../../lib/stage-badge'
import { ALL_STAGES } from '../../lib/types'

describe('StatusBadge', () => {
  it('renders icon AND label so status never relies on colour alone', () => {
    render(<StatusBadge stage="pending_review" />)
    const badge = screen.getByText('Needs review')
    expect(badge).toBeInTheDocument()
    // The icon is aria-hidden but must still be present in the DOM.
    expect(badge.querySelector('svg')).not.toBeNull()
  })

  it('says Retrying only while a retry is scheduled, otherwise "Failed – temporary"', () => {
    const { rerender } = render(<StatusBadge stage="failed" failureKind="transient" retryScheduled />)
    expect(screen.getByText('Retrying')).toBeInTheDocument()
    rerender(<StatusBadge stage="failed" failureKind="transient" />)
    expect(screen.queryByText('Retrying')).not.toBeInTheDocument()
    expect(screen.getByText('Failed – temporary')).toBeInTheDocument()
    rerender(<StatusBadge stage="failed" failureKind="permanent" />)
    expect(screen.getByText('Failed')).toBeInTheDocument()
  })

  it('shows the real stage AND a "No longer in playlist" badge for removed videos', () => {
    render(<StatusBadge stage="approved" removedUpstream />)
    expect(screen.getByText('Approved')).toBeInTheDocument()
    expect(screen.getByText('No longer in playlist')).toBeInTheDocument()
  })

  it('spins the loader icon with the atlas-spin class', () => {
    render(<StatusBadge stage="fetching_transcript" />)
    expect(screen.getByText('Fetching transcript').querySelector('svg')).toHaveClass('atlas-spin')
  })

  it('renders every stage without throwing', () => {
    for (const stage of ALL_STAGES) {
      const { unmount } = render(<StatusBadge stage={stage} />)
      expect(screen.getByText(/\w/), stage).toBeInTheDocument()
      unmount()
    }
  })
})

describe('StageDot', () => {
  it('exposes the stage as an accessible label', () => {
    render(<StageDot spec={stageToBadge('pending_review')} />)
    expect(screen.getByRole('img', { name: 'Needs review' })).toBeInTheDocument()
  })
})
