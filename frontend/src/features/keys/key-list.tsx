import { Eraser, Eye, Pencil, RefreshCw, Trash2 } from 'lucide-react'

import { ActionMenu } from '../../components/shared/action-menu'
import { KeyRowBadge } from '../../components/shared/key-status-badge'
import { RelativeTime } from '../../components/shared/relative-time'
import { Switch } from '../../components/ui/switch'
import { formatCountdown } from '../../lib/format'
import { autoDisabledNote, longestCooldown } from '../../lib/key-status-badge'
import type { KeyGroup } from '../../lib/key-groups'
import type { LlmKey } from '../../lib/types'
import { cn } from '../../lib/utils'

interface KeyListProps {
  groups: KeyGroup[]
  selected: ReadonlySet<number>
  onToggleSelected: (ids: number[], selected: boolean) => void
  onToggleEnabled: (ids: number[], enabled: boolean) => void
  onCheck: (key: LlmKey) => void
  onEdit: (key: LlmKey) => void
  onReveal: (key: LlmKey) => void
  onClearCooldowns: (key: LlmKey) => void
  onDelete: (key: LlmKey) => void
  checkingIds: ReadonlySet<number>
}

/** Keys grouped by provider (design.md 6.5): a group switch, then one row per key. */
export function KeyList({
  groups,
  selected,
  onToggleSelected,
  onToggleEnabled,
  onCheck,
  onEdit,
  onReveal,
  onClearCooldowns,
  onDelete,
  checkingIds,
}: KeyListProps) {
  return (
    <div className="space-y-5">
      {groups.map((group) => {
        const ids = group.keys.map((key) => key.id)
        const anyEnabled = group.keys.some((key) => key.enabled)
        const allSelected = ids.every((id) => selected.has(id))
        return (
          <section
            key={group.provider}
            aria-label={group.label}
            className="overflow-hidden rounded border border-border bg-surface"
          >
            <header className="flex h-row items-center justify-between gap-3 border-b border-border bg-surface-muted px-3">
              <div className="flex items-center gap-3">
                <input
                  type="checkbox"
                  aria-label={`Select all ${group.label} keys`}
                  checked={allSelected}
                  onChange={(event) => onToggleSelected(ids, event.target.checked)}
                />
                <h2 className="text-body font-medium text-text">{group.label}</h2>
                <span className="text-label text-text-muted">
                  {group.keys.length} {group.keys.length === 1 ? 'key' : 'keys'}
                </span>
              </div>
              <Switch
                checked={anyEnabled}
                onCheckedChange={(next) => onToggleEnabled(ids, next)}
                label={`${anyEnabled ? 'Disable' : 'Enable'} all ${group.label} keys`}
              />
            </header>
            <ul>
              {group.keys.map((key) => (
                <KeyRow
                  key={key.id}
                  keyRow={key}
                  selected={selected.has(key.id)}
                  checking={checkingIds.has(key.id)}
                  onToggleSelected={(next) => onToggleSelected([key.id], next)}
                  onToggleEnabled={(next) => onToggleEnabled([key.id], next)}
                  onCheck={onCheck}
                  onEdit={onEdit}
                  onReveal={onReveal}
                  onClearCooldowns={onClearCooldowns}
                  onDelete={onDelete}
                />
              ))}
            </ul>
          </section>
        )
      })}
    </div>
  )
}

function formatCap(value: number | null): string | null {
  return value ? value.toLocaleString() : null
}

interface KeyRowProps {
  keyRow: LlmKey
  selected: boolean
  checking: boolean
  onToggleSelected: (selected: boolean) => void
  onToggleEnabled: (enabled: boolean) => void
  onCheck: (key: LlmKey) => void
  onEdit: (key: LlmKey) => void
  onReveal: (key: LlmKey) => void
  onClearCooldowns: (key: LlmKey) => void
  onDelete: (key: LlmKey) => void
}

function KeyRow({
  keyRow,
  selected,
  checking,
  onToggleSelected,
  onToggleEnabled,
  onCheck,
  onEdit,
  onReveal,
  onClearCooldowns,
  onDelete,
}: KeyRowProps) {
  const cooldown = longestCooldown(keyRow.cooldowns)
  const detail = autoDisabledNote(keyRow) ?? keyRow.last_health_error
  const requestCap = formatCap(keyRow.monthly_request_cap)
  const tokenCap = formatCap(keyRow.monthly_token_cap)

  return (
    <li
      className={cn(
        'flex min-h-row flex-wrap items-center gap-x-4 gap-y-1 border-b border-border px-3 py-2 last:border-b-0 hover:bg-surface-muted',
        selected && 'bg-accent-soft',
        !keyRow.enabled && 'text-text-muted',
      )}
    >
      <input
        type="checkbox"
        aria-label={`Select ${keyRow.label}`}
        checked={selected}
        onChange={(event) => onToggleSelected(event.target.checked)}
      />

      <div className="min-w-0 flex-1 basis-56">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate text-body font-medium text-text">{keyRow.label}</span>
          <code className="font-mono text-code text-text-muted">{keyRow.masked_key}</code>
        </div>
        {detail ? (
          <p className="text-label text-st-error-fg" title={detail}>
            {detail}
          </p>
        ) : null}
        {requestCap || tokenCap ? (
          <p className="text-label text-text-muted">
            Monthly cap:{' '}
            {[requestCap && `${requestCap} requests`, tokenCap && `${tokenCap} tokens`]
              .filter(Boolean)
              .join(', ')}
          </p>
        ) : null}
      </div>

      <div className="flex items-center gap-2">
        <KeyRowBadge keyRow={keyRow} />
        {cooldown ? (
          <span className="tabular text-label text-text-muted">
            {formatCountdown(new Date(cooldown.expires_at * 1000).toISOString())} left
          </span>
        ) : null}
      </div>

      <span className="w-24 text-label text-text-muted">
        <RelativeTime iso={keyRow.last_checked_at} prefix="Checked" fallback="Never checked" />
      </span>

      <Switch
        checked={keyRow.enabled}
        onCheckedChange={onToggleEnabled}
        label={`${keyRow.enabled ? 'Disable' : 'Enable'} ${keyRow.label}`}
      />

      <ActionMenu
        label={`Actions for ${keyRow.label}`}
        actions={[
          {
            key: 'check',
            label: checking ? 'Checking…' : 'Check',
            icon: RefreshCw,
            disabled: checking,
            onSelect: () => onCheck(keyRow),
          },
          { key: 'edit', label: 'Edit', icon: Pencil, onSelect: () => onEdit(keyRow) },
          { key: 'reveal', label: 'Reveal', icon: Eye, onSelect: () => onReveal(keyRow) },
          {
            key: 'cooldowns',
            label: 'Clear cooldowns',
            icon: Eraser,
            disabled: cooldown === null,
            disabledReason: 'This key is not cooling down.',
            onSelect: () => onClearCooldowns(keyRow),
          },
          {
            key: 'delete',
            label: 'Delete',
            icon: Trash2,
            danger: true,
            onSelect: () => onDelete(keyRow),
          },
        ]}
      />
    </li>
  )
}
