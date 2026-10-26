import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import type { FormEvent, ReactNode } from 'react'

import { Button } from '../ui/button'

interface FormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Sentence case, e.g. "Add model". */
  title: string
  description?: ReactNode
  submitLabel: string
  /** Called on submit (Enter or the primary button). */
  onSubmit: () => void
  isSubmitting?: boolean
  /** Reason the primary button is disabled (also disables it). */
  submitDisabled?: boolean
  /** Inline error shown above the buttons. */
  error?: string | null
  /** Extra footer content on the left (e.g. a Test button). */
  footerStart?: ReactNode
  children: ReactNode
}

/**
 * Modal with a form body (design.md 6.4, 7). Cancel and the primary action sit on the right with
 * the primary last; while submitting the dialog cannot be dismissed.
 */
export function FormDialog({
  open,
  onOpenChange,
  title,
  description,
  submitLabel,
  onSubmit,
  isSubmitting = false,
  submitDisabled = false,
  error,
  footerStart,
  children,
}: FormDialogProps) {
  const handleSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (!isSubmitting && !submitDisabled) onSubmit()
  }

  const guard = (event: Event) => {
    if (isSubmitting) event.preventDefault()
  }

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => (isSubmitting && !next ? null : onOpenChange(next))}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-text/40 backdrop-blur-sm" />
        <Dialog.Content
          onEscapeKeyDown={guard}
          onPointerDownOutside={guard}
          onInteractOutside={guard}
          {...(description ? {} : { 'aria-describedby': undefined })}
          className="fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-[calc(100vw-2rem)] max-w-modal -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-modal border border-card-border bg-surface p-6 shadow-modal"
        >
          <form onSubmit={handleSubmit} noValidate>
            <div className="flex items-start gap-4">
              <div className="flex-1">
                <Dialog.Title className="font-display text-[22px] font-semibold leading-7 tracking-tight text-text">
                  {title}
                </Dialog.Title>
                {description ? (
                  <Dialog.Description className="mt-1 text-body text-text-muted">
                    {description}
                  </Dialog.Description>
                ) : null}
              </div>
              <Dialog.Close asChild>
                <button
                  type="button"
                  disabled={isSubmitting}
                  className="rounded-pill p-2 text-text-muted transition hover:bg-surface-muted hover:text-text disabled:opacity-50"
                  aria-label="Close dialog"
                >
                  <X aria-hidden="true" className="size-icon" />
                </button>
              </Dialog.Close>
            </div>

            <div className="mt-5 space-y-4">{children}</div>

            {error ? (
              <p role="alert" className="mt-3 text-label text-st-error-fg">
                {error}
              </p>
            ) : null}

            <div className="mt-5 flex items-center justify-between gap-3">
              <div>{footerStart}</div>
              <div className="flex gap-3">
                <Button
                  variant="secondary"
                  onClick={() => onOpenChange(false)}
                  disabled={isSubmitting}
                >
                  Cancel
                </Button>
                <Button type="submit" disabled={isSubmitting || submitDisabled}>
                  {isSubmitting ? 'Saving…' : submitLabel}
                </Button>
              </div>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

/** Labelled form row used inside dialogs. */
export function Field({
  id,
  label,
  hint,
  error,
  children,
}: {
  id: string
  label: string
  hint?: string
  error?: string | null
  children: ReactNode
}) {
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="text-label font-medium text-text">
        {label}
      </label>
      {children}
      {hint ? (
        <p id={`${id}-hint`} className="text-label text-text-muted">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="text-label text-st-error-fg">
          {error}
        </p>
      ) : null}
    </div>
  )
}
