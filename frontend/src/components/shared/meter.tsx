import { cn } from '../../lib/utils'

/** Thin usage bar; amber from 80%, red when full. `fraction` is 0..1. */
export function Meter({
  fraction,
  level = 'ok',
  className,
}: {
  fraction: number
  level?: 'ok' | 'high' | 'exhausted'
  className?: string
}) {
  const pct = Math.round(Math.min(Math.max(fraction, 0), 1) * 100)
  return (
    <div
      aria-hidden="true"
      className={cn('h-1 w-full overflow-hidden rounded-pill bg-surface-muted', className)}
    >
      <div
        className={cn(
          'h-full rounded-pill transition-[width] duration-700',
          level === 'exhausted'
            ? 'bg-st-error-fg'
            : level === 'high'
              ? 'bg-st-warn-fg'
              : 'bg-gradient-to-r from-accent to-accent-2',
        )}
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}
