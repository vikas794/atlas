import type { LucideIcon } from 'lucide-react'

import type { Tone } from '../../lib/tone'
import { TONE_BADGE } from '../../lib/tone'
import { cn } from '../../lib/utils'
import { CountUp } from './count-up'

/** Bento tile: big number, caption, tinted icon. All tiles in a row share one span. */
export function StatCard({
  label,
  value,
  hint,
  icon: Icon,
  tone = 'neutral',
}: {
  label: string
  value: number
  hint?: string
  icon: LucideIcon
  tone?: Tone
}) {
  return (
    <div data-reveal className="panel card-lift relative overflow-hidden p-5">
      <div className="flex items-start justify-between gap-3">
        <p className="text-label text-text-muted">{label}</p>
        <span
          className={cn(
            'inline-flex size-8 items-center justify-center rounded-pill',
            TONE_BADGE[tone],
          )}
        >
          <Icon aria-hidden="true" className="size-icon" />
        </span>
      </div>
      <p className="mt-3 font-display text-[40px] font-semibold leading-none tracking-tight text-text">
        <CountUp value={value} />
      </p>
      {hint ? <p className="mt-2 text-label text-text-muted">{hint}</p> : null}
    </div>
  )
}
