import {
  DndContext,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import { SortableContext, horizontalListSortingStrategy, useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { ArrowRight } from 'lucide-react'

import type { FallbackEntry } from '../../lib/types'
import { cn } from '../../lib/utils'

interface ServingOrderProps {
  /** The models that can serve now, in chain order. */
  entries: FallbackEntry[]
  /** Drag is off (unsaved table edits, or a save in flight). */
  locked: boolean
  /** Why it is locked, shown next to the label. */
  lockedHint?: string
  onReorder: (activeId: number, overId: number) => void
}

function Chip({ entry, index, locked }: { entry: FallbackEntry; index: number; locked: boolean }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: entry.id,
    disabled: locked,
  })
  return (
    <span
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      {...attributes}
      {...listeners}
      aria-label={`${entry.display_name || entry.model_id}, position ${index + 1}`}
      className={cn(
        'rounded-pill px-3 py-1 text-label touch-none select-none',
        index === 0 ? 'bg-accent text-on-accent' : 'bg-surface-muted text-text',
        locked ? 'cursor-default' : 'cursor-grab active:cursor-grabbing',
        isDragging && 'relative z-10 shadow-float',
      )}
    >
      {entry.display_name || entry.model_id}
    </span>
  )
}

/**
 * The global fallback order as draggable chips (design.md 6.4): drop a chip anywhere to re-rank it
 * across providers. The caller saves the new order; the chips are plain while `locked`.
 */
export function ServingOrder({ entries, locked, lockedHint, onReorder }: ServingOrderProps) {
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }))

  const handleDragEnd = ({ active, over }: DragEndEvent) => {
    if (locked || !over || active.id === over.id) return
    onReorder(Number(active.id), Number(over.id))
  }

  return (
    // DndContext renders hidden <div>s: keep it outside the flex row.
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
      <SortableContext items={entries.map((entry) => entry.id)} strategy={horizontalListSortingStrategy}>
        <div
          data-reveal
          className="panel mt-4 flex flex-wrap items-center gap-2 px-5 py-3"
          aria-label="Serving order"
          role="group"
        >
          <span className="mr-1 text-label text-text-muted">Serving order</span>
          {locked && lockedHint ? (
            <span className="mr-1 text-label text-text-muted">({lockedHint})</span>
          ) : null}
          {entries.map((entry, index) => (
            <span key={entry.id} className="inline-flex items-center gap-2">
              {index > 0 ? (
                <ArrowRight aria-hidden="true" className="size-icon text-text-muted" />
              ) : null}
              <Chip entry={entry} index={index} locked={locked} />
            </span>
          ))}
        </div>
      </SortableContext>
    </DndContext>
  )
}
