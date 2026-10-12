import { progressSegments, type StageCounts } from '../../lib/progress'
import { SEGMENT_COLOR } from '../../lib/tone'
import { cn } from '../../lib/utils'

interface SegmentedProgressBarProps {
  counts: StageCounts
  total: number
  className?: string
  showLabel?: boolean
}

/** Minimum width (percent) so a single video is still visible on a 250-video bar. */
const MIN_SEGMENT_PERCENT = 1.5

/**
 * design.md 7: 8px tall, rounded, segmented by status colour, with a text label
 * of counts beside it. Segment membership comes from `progressSegments`, which
 * shares its stage groups with the summary strip.
 */
export function SegmentedProgressBar({
  counts,
  total: reportedTotal,
  className,
  showLabel = true,
}: SegmentedProgressBarProps) {
  // `video_count` can lag the stage counts (or be 0); the stage counts are the live truth.
  const total = Math.max(
    reportedTotal,
    Object.values(counts).reduce((sum, count) => sum + (count ?? 0), 0),
  )
  const segments = progressSegments(counts)
  const published = segments.find((segment) => segment.key === 'published')?.count ?? 0
  const summary = segments
    .filter((segment) => segment.count > 0)
    .map((segment) => `${segment.count} ${segment.label.toLowerCase()}`)
    .join(', ')

  if (total <= 0) {
    return (
      <div className={cn('flex items-center gap-3', className)}>
        <div className="h-bar w-full rounded-pill bg-surface-muted" />
        {showLabel && <span className="shrink-0 text-label text-text-muted">0 videos</span>}
      </div>
    )
  }

  return (
    <div className={cn('flex items-center gap-3', className)}>
      <div
        role="img"
        aria-label={summary || 'No progress yet'}
        className="relative flex h-bar w-full overflow-hidden rounded-pill bg-surface-muted"
      >
        {segments.map((segment) => {
          if (segment.count <= 0) return null
          const percent = Math.max((segment.count / total) * 100, MIN_SEGMENT_PERCENT)
          return (
            <div
              key={segment.key}
              title={`${segment.label}: ${segment.count}`}
              style={{ width: `${percent}%` }}
              className={cn('h-full transition-[width]', SEGMENT_COLOR[segment.key])}
            />
          )
        })}
      </div>
      {showLabel && (
        <span className="shrink-0 text-label tabular-nums text-text-muted">
          {published} of {total} published
        </span>
      )}
    </div>
  )
}
