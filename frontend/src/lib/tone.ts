import type { SegmentKey } from './progress'

/** Semantic tone -> utility classes. Tailwind sees these literals because `src/**` is scanned. */
export type Tone = 'neutral' | 'progress' | 'review' | 'success' | 'error' | 'warn'

/** Soft background + coloured text, for badges (design.md 3.2, 7). */
export const TONE_BADGE: Record<Tone, string> = {
  neutral: 'bg-st-neutral-bg text-st-neutral-fg',
  progress: 'bg-st-progress-bg text-st-progress-fg',
  review: 'bg-st-review-bg text-st-review-fg',
  success: 'bg-st-success-bg text-st-success-fg',
  error: 'bg-st-error-bg text-st-error-fg',
  warn: 'bg-st-warn-bg text-st-warn-fg',
}

/** Text/icon colour only. */
export const TONE_TEXT: Record<Tone, string> = {
  neutral: 'text-st-neutral-fg',
  progress: 'text-st-progress-fg',
  review: 'text-st-review-fg',
  success: 'text-st-success-fg',
  error: 'text-st-error-fg',
  warn: 'text-st-warn-fg',
}

/** Progress bar segment fills. Published and approved are both success family. */
export const SEGMENT_COLOR: Record<SegmentKey, string> = {
  published: 'bg-st-success-fg',
  approved: 'bg-st-success-fg/60',
  review: 'bg-st-review-fg',
  progress: 'bg-st-progress-fg',
  failed: 'bg-st-error-fg',
  queued: 'bg-st-neutral-fg/30',
}
