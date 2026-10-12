import type { HTMLAttributes } from 'react'

import { cn } from '../../lib/utils'
import { TONE_BADGE, type Tone } from '../../lib/tone'

/** Section 3.2 semantic tones. Each pairs a colored text color with a soft background. */
export type BadgeTone = Tone

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone
}

export function Badge({ className, tone = 'neutral', ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        // Section 7: pill shape, 12px text, soft background + colored text.
        // The caller supplies an icon so status never relies on color alone.
        'inline-flex items-center gap-1 rounded-pill px-2 py-1 text-label',
        TONE_BADGE[tone],
        className,
      )}
      {...props}
    />
  )
}
