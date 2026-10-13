import { useId, type ReactNode } from 'react'

import { cn } from '../../lib/utils'

interface DisabledReasonProps {
  /** Why the wrapped control is disabled. Nothing is rendered extra when null. */
  reason: string | null | undefined
  children: ReactNode
  className?: string
}

/**
 * design.md 7: a disabled control carries a tooltip explaining why. Disabled
 * buttons swallow pointer events and are skipped by keyboard focus, so the
 * tooltip lives on a wrapper span. While a reason is shown the wrapper is a tab
 * stop, so keyboard users reach it too (design.md 8) and assistive tech reads
 * `reason` through `aria-describedby`.
 */
export function DisabledReason({ reason, children, className }: DisabledReasonProps) {
  const id = useId()

  if (!reason) return <span className={cn('inline-flex', className)}>{children}</span>

  return (
    <span
      className={cn('group relative inline-flex rounded', className)}
      tabIndex={0}
      aria-describedby={id}
    >
      {children}
      <span
        id={id}
        role="tooltip"
        className={cn(
          'pointer-events-none invisible absolute right-0 top-full z-50 mt-1 w-max max-w-xs',
          'rounded border border-border bg-surface px-3 py-1 text-label text-text shadow-popover',
          'opacity-0 transition group-focus-within:visible group-focus-within:opacity-100',
          'group-hover:visible group-hover:opacity-100',
        )}
      >
        {reason}
      </span>
    </span>
  )
}
