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
import { Badge } from '../../components/ui/badge'
import { Switch } from '../../components/ui/switch'
import {
  blockedReason,
  clampRank,
  formatContext,
  moveEntry,
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
    <div className="overflow-x-auto rounded border border-border bg-surface">
      {/* DndContext renders hidden <div>s, which are invalid inside <table>: keep it outside. */}
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext
          items={entries.map((entry) => entry.id)}
          strategy={verticalListSortingStrategy}
        >
          <table className="w-full min-w-[56rem] border-collapse text-body">
            <thead>
              <tr className="sticky top-0 h-row border-b border-border bg-surface-muted text-left text-label text-text-muted">
                <th scope="col" className="w-10 px-3">
                  <span className="sr-only">Reorder</span>
                </th>
                <th scope="col" className="w-20 px-3">
                  Rank
                </th>
                <th scope="col" className="px-3">
                  Model
                </th>
                <th scope="col" className="px-3">
                  Context
                </th>
                <th scope="col" className="px-3">
                  Quota
                </th>
                <th scope="col" className="px-3">
                  Keys
                </th>
                <th scope="col" className="px-3">
                  Enabled
                </th>
                <th scope="col" className="w-12 px-3">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
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
            </tbody>
          </table>
        </SortableContext>
      </DndContext>
    </div>
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

  return (
    <tr
      ref={setNodeRef}
      style={{ transform: CSS.Translate.toString(transform), transition }}
      className={cn(
        'h-row border-b border-border last:border-b-0 hover:bg-surface-muted',
        isDragging && 'relative z-10 bg-surface-muted shadow-popover',
      )}
    >
      <td className="px-3">
        <button
          type="button"
          ref={setActivatorNodeRef}
          aria-label={`Reorder ${name}. Press Space to lift, arrow keys to move, Space to drop.`}
          className="inline-flex cursor-grab touch-none rounded p-1 text-text-muted hover:text-text active:cursor-grabbing"
          {...attributes}
          {...listeners}
        >
          <GripVertical aria-hidden="true" className="size-icon" />
        </button>
      </td>
      <td className="px-3">
        <RankInput name={name} rank={index + 1} total={total} onCommit={onRank} />
      </td>
      <td className={cn('px-3 py-2', muted && 'opacity-60')}>
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-text">{name}</span>
          <Badge tone="neutral">{entry.provider}</Badge>
        </div>
        <div className="text-label text-text-muted">{entry.model_id}</div>
      </td>
      <td className={cn('px-3 text-text-muted', muted && 'opacity-60')}>
        {formatContext(entry.input_token_limit)}
      </td>
      <td className="px-3">
        {quota.label ? (
          <Badge
            tone={
              quota.level === 'exhausted' ? 'error' : quota.level === 'high' ? 'warn' : 'neutral'
            }
          >
            {quota.label}
          </Badge>
        ) : (
          <span className="text-label text-text-muted">No limits</span>
        )}
      </td>
      <td className="px-3">
        {entry.key_count === 0 ? (
          <Link to="/keys" className="inline-flex">
            <Badge tone="error">No keys</Badge>
          </Link>
        ) : blocked ? (
          <Badge tone="warn" title={`${entry.usable_key_count} of ${entry.key_count} keys usable`}>
            {blocked}
          </Badge>
        ) : (
          <span className="text-label text-text-muted">
            {entry.usable_key_count} of {entry.key_count}
          </span>
        )}
      </td>
      <td className="px-3">
        <Switch
          checked={entry.enabled}
          onCheckedChange={(next) => onToggle(entry.id, next)}
          label={`${entry.enabled ? 'Disable' : 'Enable'} ${name}`}
        />
      </td>
      <td className="px-3">
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
      </td>
    </tr>
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
      className="tabular h-control w-12 rounded border border-transparent bg-transparent px-1 text-center text-text hover:border-border focus:border-border"
      value={draft ?? formatPosition(rank, 2)}
      onFocus={(event) => event.currentTarget.select()}
      onChange={(event) => setDraft(event.target.value.replace(/[^0-9]/g, ''))}
      onBlur={commit}
      onKeyDown={onKeyDown}
    />
  )
}
