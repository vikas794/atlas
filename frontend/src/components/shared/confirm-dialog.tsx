import * as Dialog from '@radix-ui/react-dialog'
import { AlertTriangle, X } from 'lucide-react'
import { useState, type ReactNode } from 'react'

import { Button } from '../ui/button'

interface ConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Sentence case, e.g. "Delete playlist". */
  title: string
  /** design.md 6.4: state the consequence AND the count. */
  description: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  isDestructive?: boolean
  isLoading?: boolean
  onConfirm: () => void | Promise<unknown>
  /** Extra content between the description and the buttons (e.g. an option or field). */
  children?: ReactNode
}

/**
 * Confirmation modal (design.md 6.4). The destructive/primary button is on the
 * right. While the action runs the dialog cannot be dismissed (Escape, overlay
 * and close button are all inert). A rejected `onConfirm` is swallowed here
 * because the caller reports the error (toast); the dialog stays open so the
 * action can be retried or cancelled.
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  isDestructive = true,
  isLoading = false,
  onConfirm,
  children,
}: ConfirmDialogProps) {
  const [isRunning, setIsRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const busy = isLoading || isRunning

  const handleOpenChange = (next: boolean) => {
    if (busy && !next) return
    if (!next) setError(null)
    onOpenChange(next)
  }

  const handleConfirm = async () => {
    setIsRunning(true)
    setError(null)
    try {
      await onConfirm()
      onOpenChange(false)
    } catch (cause) {
      // Keep the dialog open and say what went wrong inside it: a toast behind the
      // modal overlay would go unseen. The caller may also toast.
      setError(cause instanceof Error && cause.message ? cause.message : 'Something went wrong.')
    } finally {
      setIsRunning(false)
    }
  }

  const preventWhileBusy = (event: Event) => {
    if (busy) event.preventDefault()
  }

  return (
    <Dialog.Root open={open} onOpenChange={handleOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-text/40" />
        <Dialog.Content
          onEscapeKeyDown={preventWhileBusy}
          onPointerDownOutside={preventWhileBusy}
          onInteractOutside={preventWhileBusy}
          className="fixed left-1/2 top-1/2 z-50 w-[calc(100vw-2rem)] max-w-modal -translate-x-1/2 -translate-y-1/2 rounded-modal border border-border bg-surface p-5 shadow-modal"
        >
          <div className="flex items-start gap-4">
            {isDestructive && (
              <div className="flex size-6 shrink-0 items-center justify-center rounded-pill bg-st-error-bg text-st-error-fg">
                <AlertTriangle aria-hidden="true" className="size-icon" />
              </div>
            )}
            <div className="flex-1">
              <Dialog.Title className="text-section text-text">{title}</Dialog.Title>
              <Dialog.Description className="mt-2 text-body text-text-muted">
                {description}
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <button
                type="button"
                disabled={busy}
                className="rounded text-text-muted transition hover:text-text disabled:cursor-not-allowed disabled:opacity-50"
                aria-label="Close dialog"
              >
                <X aria-hidden="true" className="size-icon" />
              </button>
            </Dialog.Close>
          </div>

          {children ? <div className="mt-4">{children}</div> : null}

          {error ? (
            <p role="alert" className="mt-3 text-label text-st-error-fg">
              {error}
            </p>
          ) : null}

          <div className="mt-5 flex justify-end gap-3">
            <Button variant="secondary" onClick={() => handleOpenChange(false)} disabled={busy}>
              {cancelLabel}
            </Button>
            <Button
              variant={isDestructive ? 'danger' : 'primary'}
              onClick={() => void handleConfirm()}
              disabled={busy}
            >
              {busy ? 'Working…' : confirmLabel}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
