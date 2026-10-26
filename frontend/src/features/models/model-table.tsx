import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { GripVertical, Pencil, PlayCircle, Trash2 } from 'lucide-react'
import { useState, type KeyboardEvent } from 'react'
import { Link } from 'react-router-dom'

import { ActionMenu } from '../../components/shared/action-menu'
import { Meter } from '../../components/shared/meter'
import { ProviderMark } from '../../components/shared/provider-mark'
import { Badge } from '../../components/ui/badge'
import { Switch } from '../../components/ui/switch'
import {
  blockedReason,
  clampRank,
  formatContext,
  moveEntry,
  quotaFraction,
  quotaSummary,
} from '../../lib/model-chain'
import { formatPosition } from '../../lib/format'
import type { FallbackEntry, RateLimitRow } from '../../lib/types'
import { cn } from '../../lib/utils'

interface ModelTableProps {
  entries: FallbackEntry[]
  rates: Map<number, RateLimitRow>
  /** Called with the new order after a drag, keyboard move or rank edit. */
  onReorder: (next: FallbackEntry[]) => void
  onToggle: (id: number, enabled: boolean) => void
  onEdit: (entry: FallbackEntry) => void
  onTest: (entry: FallbackEntry) => void
  onDelete: (entry: FallbackEntry) => void
}

/** Chain table (design.md 6.4): drag handle, rank, model, context, quota, keys, enabled, menu. */
export function ModelTable({
  entries,
  rates,
  onReorder,
  onToggle,
  onEdit,
  onTest,
  onDelete,
}: ModelTableProps) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  const handleDragEnd = ({ active, over }: DragEndEvent) => {
    if (!over || active.id === over.id) return
    const from = entries.findIndex((entry) => entry.id === active.id)
    const to = entries.findIndex((entry) => entry.id === over.id)
    if (from >= 0 && to >= 0) onReorder(moveEntry(entries, from, to))
  }

  return (
    // DndContext renders hidden <div>s: keep it outside the list element.
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
      <SortableContext
        items={entries.map((entry) => entry.id)}
        strategy={verticalListSortingStrategy}
      >
        <ol className="space-y-3">
          {entries.map((entry, index) => (
            <ModelRow
              key={entry.id}
              entry={entry}
              index={index}
              total={entries.length}
              rate={rates.get(entry.id)}
              onRank={(rank) => onReorder(moveEntry(entries, index, rank - 1))}
              onToggle={onToggle}
              onEdit={onEdit}
              onTest={onTest}
              onDelete={onDelete}
            />
          ))}
        </ol>
      </SortableContext>
    </DndContext>
  )
}

interface ModelRowProps {
  entry: FallbackEntry
  index: number
  total: number
  rate: RateLimitRow | undefined
  onRank: (rank: number) => void
  onToggle: (id: number, enabled: boolean) => void
  onEdit: (entry: FallbackEntry) => void
  onTest: (entry: FallbackEntry) => void
  onDelete: (entry: FallbackEntry) => void
}

function ModelRow({
  entry,
  index,
  total,
  rate,
  onRank,
  onToggle,
  onEdit,
  onTest,
  onDelete,
}: ModelRowProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    setActivatorNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: entry.id })
  const name = entry.display_name || entry.model_id
  const quota = quotaSummary(rate)
  const blocked = blockedReason(entry.blocked)
  // A model nothing can serve right now stays in the chain but reads as muted.
  const muted = !entry.enabled || entry.usable_key_count === 0

  const fraction = quotaFraction(rate)

  return (
    <li
      ref={setNodeRef}
      style={{ transform: CSS.Translate.toString(transform), transition }}
      data-reveal
      className={cn(
        'panel card-lift relative flex flex-wrap items-center gap-x-5 gap-y-3 py-4 pl-3 pr-4',
        index === 0 && 'border-accent/40',
        isDragging && 'z-10 scale-[1.01] shadow-float',
      )}
    >
      <button
        type="button"
        ref={setActivatorNodeRef}
        aria-label={`Reorder ${name}. Press Space to lift, arrow keys to move, Space to drop.`}
        className="inline-flex cursor-grab touch-none rounded-lg p-2 text-text-muted hover:bg-surface-muted hover:text-text active:cursor-grabbing"
        {...attributes}
        {...listeners}
      >
        <GripVertical aria-hidden="true" className="size-icon-lg" />
      </button>

      <div className="flex flex-col items-center">
        <RankInput name={name} rank={index + 1} total={total} onCommit={onRank} />
        {index === 0 ? (
          <span className="text-[10px] font-semibold uppercase tracking-wider text-accent">
            Primary
          </span>
        ) : null}
      </div>

      <div className={cn('flex min-w-0 flex-1 basis-64 items-center gap-3', muted && 'opacity-60')}>
        <ProviderMark name={entry.provider} size="lg" />
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate text-section text-text">{name}</span>
            <Badge tone="neutral">{entry.provider}</Badge>
          </div>
          <code className="block truncate font-mono text-code text-text-muted">
            {entry.model_id}
          </code>
        </div>
      </div>

      <div className="ml-auto flex flex-wrap items-center justify-end gap-x-5 gap-y-3">
        <div className={cn('w-16 text-center', muted && 'opacity-60')}>
          <p className="font-display text-section text-text">
            {formatContext(entry.input_token_limit)}
          </p>
          <p className="text-label text-text-muted">context</p>
        </div>

        <div className="w-44">
          {quota.label ? (
            <>
              <div className="mb-1 flex items-center justify-between gap-2">
                <Badge
                  tone={
                    quota.level === 'exhausted'
                      ? 'error'
                      : quota.level === 'high'
                        ? 'warn'
                        : 'neutral'
                  }
                >
                  {quota.label}
                </Badge>
              </div>
              <Meter fraction={fraction} level={quota.level} />
            </>
          ) : (
            <span className="text-label text-text-muted">No limits</span>
          )}
        </div>

        <div className="w-28">
          {entry.key_count === 0 ? (
            <Link to="/keys" className="inline-flex">
              <Badge tone="error">No keys</Badge>
            </Link>
          ) : blocked ? (
            <Badge
              tone="warn"
              title={`${entry.usable_key_count} of ${entry.key_count} keys usable`}
            >
              {blocked}
            </Badge>
          ) : (
            <span className="text-label text-text-muted">
              {entry.usable_key_count} of {entry.key_count}
            </span>
          )}
        </div>

        <Switch
          checked={entry.enabled}
          onCheckedChange={(next) => onToggle(entry.id, next)}
          label={`${entry.enabled ? 'Disable' : 'Enable'} ${name}`}
        />

        <ActionMenu
          label={`Actions for ${name}`}
          actions={[
            { key: 'edit', label: 'Edit', icon: Pencil, onSelect: () => onEdit(entry) },
            { key: 'test', label: 'Test', icon: PlayCircle, onSelect: () => onTest(entry) },
            {
              key: 'delete',
              label: 'Delete',
              icon: Trash2,
              danger: true,
              onSelect: () => onDelete(entry),
            },
          ]}
        />
      </div>
    </li>
  )
}

/** Editable rank (design.md 6.4): type a number and press Enter or leave the field to jump there. */
function RankInput({
  name,
  rank,
  total,
  onCommit,
}: {
  name: string
  rank: number
  total: number
  onCommit: (rank: number) => void
}) {
  const [draft, setDraft] = useState<string | null>(null)

  const commit = () => {
    if (draft === null) return
    const parsed = Number(draft)
    setDraft(null)
    if (!Number.isFinite(parsed) || draft.trim() === '') return
    const target = clampRank(parsed - 1, total) + 1
    if (target !== rank) onCommit(target)
  }

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      event.preventDefault()
      commit()
    } else if (event.key === 'Escape') {
      setDraft(null)
    }
  }

  return (
    <input
      type="text"
      inputMode="numeric"
      aria-label={`Rank of ${name}`}
      title="Type a number to move this model to that rank"
      className="tabular h-10 w-14 rounded-lg border border-transparent bg-transparent px-1 text-center font-display text-[24px] font-semibold text-text hover:border-border focus:border-accent"
      value={draft ?? formatPosition(rank, 2)}
      onFocus={(event) => event.currentTarget.select()}
      onChange={(event) => setDraft(event.target.value.replace(/[^0-9]/g, ''))}
      onBlur={commit}
      onKeyDown={onKeyDown}
    />
  )
}
