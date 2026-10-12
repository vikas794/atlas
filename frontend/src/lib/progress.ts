import { ACTIVE_STAGES, type Stage } from './types'

/**
 * Summary-strip groups. The count on each card and the table filter it applies
 * are both derived from `STAGE_FILTERS`, so they can never disagree (the
 * "In progress" card is the union of ACTIVE_STAGES).
 */
export type StageFilter =
  | 'all'
  | 'queued'
  | 'in_progress'
  | 'pending_review'
  | 'approved'
  | 'published'
  | 'failed'

/** Stages each filter covers; `null` means "no stage filter". */
export const STAGE_FILTERS: Record<StageFilter, readonly Stage[] | null> = {
  all: null,
  queued: ['queued'],
  in_progress: ACTIVE_STAGES,
  pending_review: ['pending_review'],
  approved: ['approved'],
  published: ['published'],
  failed: ['failed'],
}

export type StageCounts = Partial<Record<Stage, number>>

/** Count of videos a filter selects, from the playlist's per-stage counts. */
export function filterCount(
  counts: StageCounts | undefined,
  filter: StageFilter,
  total: number,
): number {
  const stages = STAGE_FILTERS[filter]
  if (!stages) return total
  return stages.reduce((sum, stage) => sum + (counts?.[stage] ?? 0), 0)
}

export type SegmentKey = 'published' | 'approved' | 'review' | 'progress' | 'failed' | 'queued'

export interface ProgressSegment {
  key: SegmentKey
  label: string
  count: number
}

/** Segments of the progress bar; `progress` uses the same stages as the card. */
export function progressSegments(counts: StageCounts | undefined): ProgressSegment[] {
  return [
    { key: 'published', label: 'Published', count: filterCount(counts, 'published', 0) },
    { key: 'approved', label: 'Approved', count: filterCount(counts, 'approved', 0) },
    { key: 'review', label: 'Needs review', count: filterCount(counts, 'pending_review', 0) },
    { key: 'progress', label: 'In progress', count: filterCount(counts, 'in_progress', 0) },
    { key: 'failed', label: 'Failed', count: filterCount(counts, 'failed', 0) },
    { key: 'queued', label: 'Queued', count: filterCount(counts, 'queued', 0) },
  ]
}
