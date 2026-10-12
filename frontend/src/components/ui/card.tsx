import type { HTMLAttributes } from 'react'

import { cn } from '../../lib/utils'

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        // Section 2: flat surface, 1px border, 6px radius, no shadow.
        'rounded border border-border bg-surface',
        className,
      )}
      {...props}
    />
  )
}
