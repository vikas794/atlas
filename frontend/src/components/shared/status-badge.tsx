import {
  Archive,
  Check,
  Clock,
  CloudCheck,
  Eye,
  FileText,
  HelpCircle,
  Loader,
  RefreshCw,
  XCircle,
  type LucideIcon,
} from 'lucide-react'

import { Badge } from '../ui/badge'
import { cn } from '../../lib/utils'
import { stageToBadge, type BadgeSpec } from '../../lib/stage-badge'
import { TONE_TEXT } from '../../lib/tone'
import type { FailureKind, Stage } from '../../lib/types'

/**
 * Icon registry for stage badges. design.md section 8: status must never rely on
 * colour alone, so every badge ships an icon AND a text label.
 */
const ICONS: Record<string, LucideIcon> = {
  archive: Archive,
  check: Check,
  clock: Clock,
  'cloud-check': CloudCheck,
  eye: Eye,
  'file-text': FileText,
  'help-circle': HelpCircle,
  loader: Loader,
  refresh: RefreshCw,
  'x-circle': XCircle,
}

function SpecIcon({ spec, className }: { spec: BadgeSpec; className?: string }) {
  const Icon = ICONS[spec.icon] ?? HelpCircle
  return (
    <Icon
      aria-hidden="true"
      // `atlas-spin` is defined in index.css and keeps spinning under reduced motion.
      className={cn('size-icon shrink-0', spec.icon === 'loader' && 'atlas-spin', className)}
    />
  )
}

interface StatusBadgeProps {
  stage: Stage
  failureKind?: FailureKind | null
  /** True while an automatic retry is scheduled (otherwise "Failed – temporary"). */
  retryScheduled?: boolean
  removedUpstream?: boolean
  className?: string
}

/**
 * Renders the single source of truth for a video's stage. Every surface must go
 * through this (or `stageToBadge`) so the table, strip and review screen agree.
 * A removed-upstream video keeps its real stage badge and gains a second
 * "No longer in playlist" badge.
 */
export function StatusBadge({
  stage,
  failureKind,
  retryScheduled = false,
  removedUpstream = false,
  className,
}: StatusBadgeProps) {
  const spec = stageToBadge(stage, failureKind, { removedUpstream, retryScheduled })

  return (
    <span className={cn('inline-flex flex-nowrap items-center gap-1', className)}>
      <Badge tone={spec.tone} className="whitespace-nowrap">
        <SpecIcon spec={spec} />
        {spec.label}
      </Badge>
      {spec.secondary ? (
        <Badge tone={spec.secondary.tone} className="whitespace-nowrap">
          <SpecIcon spec={spec.secondary} />
          {spec.secondary.label}
        </Badge>
      ) : null}
    </span>
  )
}

/** Compact stage marker for dense lists (review rail): icon + accessible label. */
export function StageDot({ spec, className }: { spec: BadgeSpec; className?: string }) {
  return (
    <span
      role="img"
      aria-label={spec.label}
      title={spec.label}
      className={cn('inline-flex shrink-0', TONE_TEXT[spec.tone], className)}
    >
      <SpecIcon spec={spec} />
    </span>
  )
}
