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
import { ChevronDown, GripVertical, Pencil, PlayCircle, Trash2 } from 'lucide-react'
import { useState, type KeyboardEvent } from 'react'
import { Link } from 'react-router-dom'

import { ActionMenu } from '../../components/shared/action-menu'
import { Meter } from '../../components/shared/meter'
import { ProviderMark } from '../../components/shared/provider-mark'
import { EmptyState } from '../../components/shared/state-panels'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Switch } from '../../components/ui/switch'
import {
  blockedReason,
  clampRank,
  filterEntries,
  formatContext,
  groupByProvider,
  moveEntry,
  primaryIndex,
  quotaFraction,
  quotaSummary,
  withEnabledKey,
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
  /** Enable or disable several models in one staged change (a provider's select/deselect all). */
  onToggleMany: (ids: number[], enabled: boolean) => void
  onEdit: (entry: FallbackEntry) => void
  onTest: (entry: FallbackEntry) => void
  onDelete: (entry: FallbackEntry) => void
  /** Search text; only matching models are listed and reordering is off while it is set. */
  query: string
  onClearSearch: () => void
}

/**
 * Chain table (design.md 6.4), grouped by provider: drag handle, rank, model, context, quota,
 * keys, enabled, menu. Only providers with a key switched on are listed; the rank is the model's
 * place in the whole chain, hidden models included. Toggling a model moves it (see `setEnabled`);
 * "Primary" marks the first enabled model in the whole chain. A search only filters the view:
 * ranks stay put, matching groups open, and drag, the rank box and group headers are off.
 */
export function ModelTable({
  entries,
  rates,
  onReorder,
  onToggle,
  onToggleMany,
  onEdit,
  onTest,
  onDelete,
  query,
  onClearSearch,
}: ModelTableProps) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  const searching = query.trim() !== ''
  const listed = withEnabledKey(entries)
  const groups = groupByProvider(filterEntries(listed, query))
  const totals = new Map<string, number>()
  for (const entry of listed) totals.set(entry.provider, (totals.get(entry.provider) ?? 0) + 1)
  const primary = primaryIndex(entries)
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set())
  const toggleGroup = (provider: string) =>
    setCollapsed((current) => {
      const next = new Set(current)
      if (!next.delete(provider)) next.add(provider)
      return next
    })

  const handleDragEnd = ({ active, over }: DragEndEvent) => {
    if (searching || !over || active.id === over.id) return
    const from = entries.findIndex((entry) => entry.id === active.id)
    const to = entries.findIndex((entry) => entry.id === over.id)
    // Dragging reorders within a provider; the rank box moves a model anywhere in the chain.
    if (from < 0 || to < 0 || entries[from].provider !== entries[to].provider) return
    onReorder(moveEntry(entries, from, to))
  }

  return (
    // DndContext renders hidden <div>s: keep it outside the list element.
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
      <div className="space-y-6">
        {searching && groups.length === 0 ? (
          <EmptyState title="No models match your search.">
            <Button className="mt-3" variant="secondary" onClick={onClearSearch}>
              Clear search
            </Button>
          </EmptyState>
        ) : null}
        {groups.map((group) => {
          // A search opens every group that has matches; the user's own collapse state is kept.
          const open = searching || !collapsed.has(group.provider)
          const panelId = `models-${group.provider}`
          const count = group.entries.length
          return (
            <section key={group.provider} aria-label={`${group.provider} models`}>
              <h2 className={cn('flex items-center gap-2', open && 'mb-3')}>
                <button
                  type="button"
                  aria-expanded={open}
                  aria-controls={panelId}
                  disabled={searching}
                  onClick={() => toggleGroup(group.provider)}
                  className="flex min-w-0 flex-1 items-center gap-3 rounded-lg p-1 text-left hover:bg-surface-muted disabled:hover:bg-transparent"
                >
                  <ProviderMark name={group.provider} size="lg" />
                  <span className="text-section capitalize text-text">{group.provider}</span>
                  <Badge tone="neutral">
                    {searching
                      ? `${count} of ${totals.get(group.provider) ?? count}`
                      : `${count} ${count === 1 ? 'model' : 'models'}`}
                  </Badge>
                  <ChevronDown
                    aria-hidden="true"
                    className={cn(
                      'ml-auto size-icon-lg text-text-muted transition-transform',
                      !open && '-rotate-90',
                    )}
                  />
                </button>
                <button
                  type="button"
                  aria-label={
                    searching
                      ? `Select ${count} matching ${group.provider} models`
                      : `Select all ${group.provider} models`
                  }
                  disabled={group.entries.every((entry) => entry.enabled)}
                  onClick={() =>
                    onToggleMany(
                      group.entries.map((entry) => entry.id),
                      true,
                    )
                  }
                  className="rounded-lg px-2 py-1 text-label font-normal text-text-muted hover:bg-surface-muted hover:text-text disabled:opacity-40 disabled:hover:bg-transparent"
                >
                  {searching ? `Select ${count} matching` : 'Select all'}
                </button>
                <button
                  type="button"
                  aria-label={
                    searching
                      ? `Deselect ${count} matching ${group.provider} models`
                      : `Deselect all ${group.provider} models`
                  }
                  disabled={group.entries.every((entry) => !entry.enabled)}
                  onClick={() =>
                    onToggleMany(
                      group.entries.map((entry) => entry.id),
                      false,
                    )
                  }
                  className="rounded-lg px-2 py-1 text-label font-normal text-text-muted hover:bg-surface-muted hover:text-text disabled:opacity-40 disabled:hover:bg-transparent"
                >
                  {searching ? `Deselect ${count} matching` : 'Deselect all'}
                </button>
              </h2>
              <div id={panelId} hidden={!open}>
                {open ? (
                  <SortableContext
                    items={group.entries.map((entry) => entry.id)}
                    strategy={verticalListSortingStrategy}
                  >
                    <ol className="space-y-3">
                      {group.entries.map((entry) => {
                        const index = entries.findIndex((item) => item.id === entry.id)
                        return (
                          <ModelRow
                            key={entry.id}
                            entry={entry}
                            index={index}
                            total={entries.length}
                            isPrimary={index === primary}
                            searching={searching}
                            rate={rates.get(entry.id)}
                            onRank={(rank) => onReorder(moveEntry(entries, index, rank - 1))}
                            onToggle={onToggle}
                            onEdit={onEdit}
                            onTest={onTest}
                            onDelete={onDelete}
                          />
                        )
                      })}
                    </ol>
                  </SortableContext>
                ) : null}
              </div>
            </section>
          )
        })}
      </div>
    </DndContext>
  )
}

interface ModelRowProps {
  entry: FallbackEntry
  index: number
  total: number
  /** The first enabled model in the whole chain. */
  isPrimary: boolean
  /** A search is active: drag and the rank box are off. */
  searching: boolean
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
  isPrimary,
  searching,
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
  } = useSortable({ id: entry.id, disabled: searching })
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
        isPrimary && 'border-accent/40',
        isDragging && 'z-10 scale-[1.01] shadow-float',
      )}
    >
      <button
        type="button"
        ref={setActivatorNodeRef}
        aria-label={
          searching
            ? `Reordering ${name} is unavailable while searching`
            : `Reorder ${name}. Press Space to lift, arrow keys to move, Space to drop.`
        }
        title={searching ? 'Clear search to reorder' : undefined}
        className={cn(
          'inline-flex touch-none rounded-lg p-2 text-text-muted',
          searching
            ? 'cursor-not-allowed opacity-40'
            : 'cursor-grab hover:bg-surface-muted hover:text-text active:cursor-grabbing',
        )}
        {...attributes}
        {...(searching ? { 'aria-disabled': true } : listeners)}
      >
        <GripVertical aria-hidden="true" className="size-icon-lg" />
      </button>

      <div className="flex flex-col items-center">
        <RankInput
          name={name}
          rank={index + 1}
          total={total}
          readOnly={searching}
          onCommit={onRank}
        />
        {isPrimary ? (
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
  readOnly,
  onCommit,
}: {
  name: string
  rank: number
  total: number
  /** While a search is active the box only shows the rank; the draft is ignored. */
  readOnly: boolean
  onCommit: (rank: number) => void
}) {
  const [draft, setDraft] = useState<string | null>(null)

  const commit = () => {
    if (readOnly || draft === null) return
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
      title={readOnly ? 'Clear search to reorder' : 'Type a number to move this model to that rank'}
      readOnly={readOnly}
      className="tabular h-10 w-14 rounded-lg border border-transparent bg-transparent px-1 text-center font-display text-[24px] font-semibold text-text hover:border-border focus:border-accent"
      value={readOnly ? formatPosition(rank, 2) : (draft ?? formatPosition(rank, 2))}
      onFocus={(event) => event.currentTarget.select()}
      onChange={(event) => {
        if (!readOnly) setDraft(event.target.value.replace(/[^0-9]/g, ''))
      }}
      onBlur={commit}
      onKeyDown={onKeyDown}
    />
  )
}
