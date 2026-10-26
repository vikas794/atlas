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
      className="sticky bottom-0 z-30 -mx-5 mt-5 border-t border-border bg-surface px-5 py-3"
    >
      <div className="mx-auto flex max-w-page items-center justify-between gap-3">
        <span role="status" className="text-body text-text">
          Unsaved changes{count ? ` (${count})` : ''}
        </span>
        <div className="flex gap-3">
          <Button variant="secondary" onClick={onDiscard} disabled={isSaving}>
            Discard
          </Button>
          <Button onClick={onSave} disabled={isSaving}>
            {isSaving ? 'Saving…' : 'Save'}
          </Button>
        </div>
      </div>
    </div>
  )
}
