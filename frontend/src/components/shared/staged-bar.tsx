import { Button } from '../ui/button'

interface StagedBarProps {
  /** Number of staged edits, shown in the label. */
  count?: number
  onDiscard: () => void
  onSave: () => void
  isSaving?: boolean
}

/**
 * Sticky bottom bar for staged edits (design.md 6.4): "Unsaved changes" with Discard (secondary)
 * and Save (primary). Render it only while something is staged.
 */
export function StagedBar({ count, onDiscard, onSave, isSaving = false }: StagedBarProps) {
  return (
    <div
      role="region"
      aria-label="Unsaved changes"
      className="glass fixed bottom-5 left-1/2 z-40 flex w-[min(34rem,calc(100vw-2rem))] -translate-x-1/2 items-center justify-between gap-3 rounded-pill py-2 pl-5 pr-2 shadow-float"
    >
      <span role="status" className="flex items-center gap-2 text-body font-medium text-text">
        <span aria-hidden="true" className="size-2 rounded-pill bg-st-review-fg" />
        Unsaved changes{count ? ` (${count})` : ''}
      </span>
      <div className="flex gap-2">
        <Button variant="secondary" onClick={onDiscard} disabled={isSaving}>
          Discard
        </Button>
        <Button onClick={onSave} disabled={isSaving}>
          {isSaving ? 'Saving…' : 'Save'}
        </Button>
      </div>
    </div>
  )
}
