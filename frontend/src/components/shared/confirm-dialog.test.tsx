import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { ConfirmDialog } from './confirm-dialog'

function Harness({
  onConfirm,
  isLoading,
}: {
  onConfirm: () => void | Promise<unknown>
  isLoading?: boolean
}) {
  const [open, setOpen] = useState(true)
  return (
    <>
      <span data-testid="state">{open ? 'open' : 'closed'}</span>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title="Delete playlist"
        description="Delete and all 187 stored videos?"
        confirmLabel="Delete playlist"
        isLoading={isLoading}
        onConfirm={onConfirm}
      />
    </>
  )
}

describe('ConfirmDialog', () => {
  it('renders the consequence text, the danger button on the right and closes after success', async () => {
    const user = userEvent.setup()
    render(<Harness onConfirm={vi.fn().mockResolvedValue(undefined)} />)

    expect(screen.getByRole('heading', { name: 'Delete playlist' })).toBeInTheDocument()
    expect(screen.getByText('Delete and all 187 stored videos?')).toBeInTheDocument()
    const buttons = screen.getAllByRole('button').filter((button) => button.textContent !== '')
    expect(buttons.at(-1)).toHaveTextContent('Delete playlist')

    await user.click(screen.getByRole('button', { name: 'Delete playlist' }))
    await waitFor(() => expect(screen.getByTestId('state')).toHaveTextContent('closed'))
  })

  it('catches a rejected onConfirm: stays open and shows the message in the dialog', async () => {
    const user = userEvent.setup()
    const onConfirm = vi.fn().mockRejectedValue(new Error('Backend said no'))
    render(<Harness onConfirm={onConfirm} />)

    await user.click(screen.getByRole('button', { name: 'Delete playlist' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Backend said no')
    expect(screen.getByTestId('state')).toHaveTextContent('open')
    // The button is usable again so the action can be retried.
    expect(screen.getByRole('button', { name: 'Delete playlist' })).toBeEnabled()
  })

  it('cannot be dismissed with Escape or the close button while the action runs', async () => {
    const user = userEvent.setup()
    let finish: () => void = () => {}
    const onConfirm = () => new Promise<void>((resolve) => (finish = resolve))
    render(<Harness onConfirm={onConfirm} />)

    await user.click(screen.getByRole('button', { name: 'Delete playlist' }))
    expect(await screen.findByRole('button', { name: /working/i })).toBeDisabled()

    await user.keyboard('{Escape}')
    expect(screen.getByTestId('state')).toHaveTextContent('open')
    expect(screen.getByRole('button', { name: 'Close dialog' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()

    finish()
    await waitFor(() => expect(screen.getByTestId('state')).toHaveTextContent('closed'))
  })

  it('honours an external isLoading flag too', () => {
    render(<Harness onConfirm={vi.fn()} isLoading />)
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
  })

  it('uses the modal tokens and no no-op animation classes', () => {
    render(<Harness onConfirm={vi.fn()} />)
    const dialog = screen.getByRole('dialog')
    expect(dialog.className).toContain('rounded-modal')
    expect(dialog.className).toContain('max-w-modal')
    expect(dialog.className).toContain('shadow-modal')
    expect(document.body.innerHTML).not.toMatch(/animate-in/)
  })
})
