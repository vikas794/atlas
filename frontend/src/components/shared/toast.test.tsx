import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ToastProvider, TOAST_DISMISS_MS, useToast } from './toast'

function Trigger() {
  const { toast } = useToast()
  return (
    <>
      <button onClick={() => toast('Saved it')}>ok</button>
      <button onClick={() => toast('Broke it', 'error')}>fail</button>
      <button onClick={() => toast('FYI', 'info')}>info</button>
    </>
  )
}

function setup() {
  return render(
    <ToastProvider>
      <Trigger />
    </ToastProvider>,
  )
}

afterEach(() => {
  vi.useRealTimers()
})

describe('toasts', () => {
  it('use role=status for success and info, role=alert for errors', async () => {
    const user = userEvent.setup()
    setup()

    await user.click(screen.getByText('ok'))
    await user.click(screen.getByText('info'))
    await user.click(screen.getByText('fail'))

    expect(screen.getByText('Saved it').closest('[role]')).toHaveAttribute('role', 'status')
    expect(screen.getByText('FYI').closest('[role]')).toHaveAttribute('role', 'status')
    expect(screen.getByText('Broke it').closest('[role]')).toHaveAttribute('role', 'alert')
  })

  it('auto-dismisses success after 4s but keeps errors until dismissed', () => {
    vi.useFakeTimers()
    setup()

    act(() => screen.getByText('ok').click())
    act(() => screen.getByText('fail').click())
    expect(screen.getByText('Saved it')).toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(TOAST_DISMISS_MS - 1)
    })
    expect(screen.getByText('Saved it')).toBeInTheDocument()
    act(() => {
      vi.advanceTimersByTime(2)
    })
    expect(screen.queryByText('Saved it')).not.toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(60_000)
    })
    expect(screen.getByText('Broke it')).toBeInTheDocument()

    act(() => screen.getByRole('button', { name: 'Dismiss notification' }).click())
    expect(screen.queryByText('Broke it')).not.toBeInTheDocument()
  })
})
