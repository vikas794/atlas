import { AlertCircle } from 'lucide-react'
import type { ReactNode } from 'react'

import { cn } from '../../lib/utils'
import { Button } from '../ui/button'

/** Plain-sentence empty state (design.md 6.4: never a blank area). */
export function EmptyState({
  title,
  children,
  className,
}: {
  title: string
  children?: ReactNode
  className?: string
}) {
  return (
    <div className={cn('rounded border border-border bg-surface p-5 text-center', className)}>
      <p className="text-body text-text">{title}</p>
      {children ? <p className="mt-1 text-label text-text-muted">{children}</p> : null}
    </div>
  )
}

/** Failed-load state with a Retry action, so an error never reads as "no data". */
export function ErrorState({
  title,
  message,
  onRetry,
  className,
  children,
}: {
  title: string
  message?: string
  onRetry?: () => void
  className?: string
  children?: ReactNode
}) {
  return (
    <div
      role="alert"
      className={cn(
        'flex flex-col items-center gap-3 rounded border border-st-error-fg/40 bg-st-error-bg p-5 text-center',
        className,
      )}
    >
      <AlertCircle aria-hidden="true" className="size-icon-lg text-st-error-fg" />
      <div>
        <p className="text-body font-medium text-st-error-fg">{title}</p>
        {message ? <p className="mt-1 text-label text-text-muted">{message}</p> : null}
      </div>
      {onRetry ? (
        <Button variant="secondary" onClick={onRetry}>
          Retry
        </Button>
      ) : null}
      {children}
    </div>
  )
}

/** Placeholder bar for skeleton rows. */
export function SkeletonBar({ className }: { className?: string }) {
  return <span className={cn('block h-3 animate-pulse rounded bg-surface-muted', className)} />
}

/**
 * design.md 6.4: skeleton rows while a table loads. Renders `<tr>`s for tables.
 * `widths` sets one bar width class per column.
 */
export function SkeletonTableRows({ rows = 5, widths }: { rows?: number; widths: string[] }) {
  return (
    <>
      {Array.from({ length: rows }, (_, row) => (
        <tr key={row} className="h-row border-b border-border last:border-b-0" aria-hidden="true">
          {widths.map((width, column) => (
            <td key={column} className="px-3 py-1">
              <SkeletonBar className={width} />
            </td>
          ))}
        </tr>
      ))}
    </>
  )
}

/** Skeleton list rows (review rail), each 40px. */
export function SkeletonListRows({ rows = 8 }: { rows?: number }) {
  return (
    <div aria-hidden="true">
      {Array.from({ length: rows }, (_, row) => (
        <div key={row} className="flex h-row items-center gap-2 border-b border-border px-3">
          <SkeletonBar className="w-6" />
          <SkeletonBar className="flex-1" />
        </div>
      ))}
    </div>
  )
}
