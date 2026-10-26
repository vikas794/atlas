import {
  AlertTriangle,
  Check,
  Clock,
  HelpCircle,
  Timer,
  XCircle,
  type LucideIcon,
} from 'lucide-react'

import { keyBadge, keyStatusToBadge } from '../../lib/key-status-badge'
import type { BadgeSpec } from '../../lib/stage-badge'
import type { KeyStatus, LlmKey } from '../../lib/types'
import { Badge } from '../ui/badge'

const ICONS: Record<string, LucideIcon> = {
  alert: AlertTriangle,
  check: Check,
  clock: Clock,
  'help-circle': HelpCircle,
  timer: Timer,
  'x-circle': XCircle,
}

function BadgeBody({ spec }: { spec: BadgeSpec }) {
  const Icon = ICONS[spec.icon] ?? HelpCircle
  return (
    <Badge tone={spec.tone} className="whitespace-nowrap">
      <Icon aria-hidden="true" className="size-icon shrink-0" />
      {spec.label}
    </Badge>
  )
}

/** Key status as icon + label + colour, always through `keyStatusToBadge` (design.md 6.5, 9). */
export function KeyStatusBadge({
  status,
  coolingDown = false,
}: {
  status: KeyStatus
  coolingDown?: boolean
}) {
  return <BadgeBody spec={keyStatusToBadge(status, coolingDown)} />
}

/** Badge for a whole key row, folding in its active cooldowns. */
export function KeyRowBadge({ keyRow }: { keyRow: Pick<LlmKey, 'status' | 'cooldowns'> }) {
  return <BadgeBody spec={keyBadge(keyRow)} />
}
