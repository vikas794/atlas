import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { DisabledReason } from './disabled-reason'

describe('DisabledReason', () => {
  it('lets keyboard users reach the reason of a disabled control', async () => {
    const user = userEvent.setup()
    render(
      <>
        <button type="button">Before</button>
        <DisabledReason reason="No videos are waiting for review">
          <button type="button" disabled>
            Approve all (0)
          </button>
        </DisabledReason>
      </>,
    )

    await user.tab()
    await user.tab()
    const wrapper = screen.getByRole('tooltip').parentElement!
    expect(wrapper).toHaveFocus()
    expect(wrapper).toHaveAccessibleDescription('No videos are waiting for review')
  })

  it('adds no tab stop when the control is enabled', async () => {
    const user = userEvent.setup()
    render(
      <DisabledReason reason={null}>
        <button type="button">Approve all (2)</button>
      </DisabledReason>,
    )

    await user.tab()
    expect(screen.getByRole('button', { name: 'Approve all (2)' })).toHaveFocus()
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
  })
})
