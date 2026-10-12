import { useNow } from '../../lib/use-now'
import { formatExactTime, formatRelativeTime, toDateTimeAttr } from '../../lib/format'

interface RelativeTimeProps {
  iso: string | null | undefined
  /** Text before the time, e.g. "Generated". */
  prefix?: string
  /** Shown when there is no valid timestamp. */
  fallback?: string
  className?: string
}

/** design.md section 8: relative time with the exact time in a tooltip. */
export function RelativeTime({ iso, prefix, fallback = '–', className }: RelativeTimeProps) {
  const now = useNow()
  const relative = formatRelativeTime(iso, now)

  if (!relative) return <span className={className}>{fallback}</span>

  return (
    <time dateTime={toDateTimeAttr(iso)} title={formatExactTime(iso)} className={className}>
      {prefix ? `${prefix} ${relative}` : relative}
    </time>
  )
}
