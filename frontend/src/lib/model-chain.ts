import type { FallbackEntry, FallbackUpdate, RateLimitRow, RateWindow } from './types'

/** Pure helpers for the staged fallback chain on the Models screen (design.md 6.4). */

/** Move one entry; out-of-range targets are clamped. Returns a new array. */
export function moveEntry<T>(entries: readonly T[], from: number, to: number): T[] {
  if (from < 0 || from >= entries.length) return [...entries]
  const target = clampRank(to, entries.length)
  const next = [...entries]
  const [moved] = next.splice(from, 1)
  next.splice(target, 0, moved)
  return next
}

/** A provider's models, in chain order. */
export interface ProviderGroup {
  provider: string
  entries: FallbackEntry[]
}

/** Models whose provider has at least one key switched on in the Keys tab. */
export function withEnabledKey(entries: readonly FallbackEntry[]): FallbackEntry[] {
  return entries.filter((entry) => entry.enabled_key_count > 0)
}

/**
 * The models the table lists: a key is switched on for the provider and the model is not hidden,
 * unless the user asked to see hidden models (`showHidden`) or those without a key (`showNoKey`).
 */
export function listedEntries(
  entries: readonly FallbackEntry[],
  showHidden: boolean,
  showNoKey = false,
): FallbackEntry[] {
  return entries.filter(
    (entry) =>
      (showHidden || !entry.hidden) && (showNoKey || entry.enabled_key_count > 0),
  )
}

/** Entries whose name, model id or provider contains the query (trimmed, case-insensitive). */
export function filterEntries(entries: readonly FallbackEntry[], query: string): FallbackEntry[] {
  const needle = query.trim().toLowerCase()
  if (needle === '') return [...entries]
  return entries.filter((entry) =>
    [entry.display_name, entry.model_id, entry.provider].some((field) =>
      field?.toLowerCase().includes(needle),
    ),
  )
}

/** Group by provider; groups and the models inside keep chain order (a group sits where its first model does). */
export function groupByProvider(entries: readonly FallbackEntry[]): ProviderGroup[] {
  const groups = new Map<string, FallbackEntry[]>()
  for (const entry of entries) {
    const list = groups.get(entry.provider)
    if (list) list.push(entry)
    else groups.set(entry.provider, [entry])
  }
  return [...groups].map(([provider, list]) => ({ provider, entries: list }))
}

/** Clamp a zero-based index into `0..length-1`. */
export function clampRank(index: number, length: number): number {
  return Math.min(Math.max(Math.trunc(index), 0), Math.max(length - 1, 0))
}

/**
 * Enable or disable models (by id) and move only the ones whose state changed. Disabled models go
 * to the end of the whole chain; enabled ones go right after the last enabled entry (hidden models
 * included). Moved models keep their current relative order. Other entries stay where they are.
 */
export function setEnabled(
  entries: readonly FallbackEntry[],
  modelIds: number | readonly number[],
  enabled: boolean,
): FallbackEntry[] {
  const ids = new Set(typeof modelIds === 'number' ? [modelIds] : modelIds)
  // A hidden model is never switched on.
  const flips = (entry: FallbackEntry) =>
    ids.has(entry.id) && entry.enabled !== enabled && !(enabled && entry.hidden)
  const changed = entries.filter(flips)
  if (changed.length === 0) return [...entries]
  const moved = changed.map((entry) => ({ ...entry, enabled, chain_enabled: enabled }))
  const rest = entries.filter((entry) => !flips(entry))
  if (!enabled) return [...rest, ...moved]
  const lastEnabled = rest.map((entry) => entry.enabled).lastIndexOf(true)
  return [...rest.slice(0, lastEnabled + 1), ...moved, ...rest.slice(lastEnabled + 1)]
}

/** A model the chain can serve right now: switched on and with a usable key (the Serving order strip). */
export function isServing(entry: FallbackEntry): boolean {
  return entry.enabled && entry.usable_key_count > 0
}

/**
 * Move one serving model onto another's place in the Serving order strip. The serving models are
 * reordered among the slots they already occupy; every other entry (disabled, no key, hidden) keeps
 * its exact position, so it returns to the same slot when it can serve again. Returns a new array.
 */
export function reorderServing(
  entries: readonly FallbackEntry[],
  activeId: number,
  overId: number,
): FallbackEntry[] {
  const slots: number[] = []
  const serving: FallbackEntry[] = []
  entries.forEach((entry, index) => {
    if (!isServing(entry)) return
    slots.push(index)
    serving.push(entry)
  })
  const from = serving.findIndex((entry) => entry.id === activeId)
  const to = serving.findIndex((entry) => entry.id === overId)
  if (from < 0 || to < 0 || from === to) return [...entries]
  const next = [...entries]
  moveEntry(serving, from, to).forEach((entry, i) => {
    next[slots[i]] = entry
  })
  return next
}

/** Index of the first enabled entry in the whole chain (the model tried first), or -1. */
export function primaryIndex(entries: readonly FallbackEntry[]): number {
  return entries.findIndex((entry) => entry.enabled)
}

/** Screen-reader message for a toggle: what changed and where it landed; '' when nothing did. */
export function toggleMessage(
  before: readonly FallbackEntry[],
  after: readonly FallbackEntry[],
  enabled: boolean,
): string {
  const wasEnabled = new Map(before.map((entry) => [entry.id, entry.enabled]))
  const changed = after.filter((entry) => wasEnabled.get(entry.id) !== entry.enabled)
  const state = enabled ? 'enabled' : 'disabled'
  if (changed.length === 0) return ''
  if (changed.length > 1) return `${changed.length} models ${state}`
  const [entry] = changed
  const rank = after.findIndex((item) => item.id === entry.id) + 1
  return `${entry.display_name || entry.model_id} ${state}, moved to rank ${rank}`
}

/** A staged edit of the chain: order plus enable flags, by model id. */
export interface StagedEntry {
  id: number
  enabled: boolean
}

export function toStaged(entries: readonly FallbackEntry[]): StagedEntry[] {
  return entries.map((entry) => ({ id: entry.id, enabled: entry.enabled }))
}

/**
 * The chain to show: the saved entries (fresh key counts and limits) in the staged order with the
 * staged enable flags. Models added elsewhere since staging are appended; deleted ones drop out,
 * so a refetch never discards staged edits.
 */
export function applyStaged(
  saved: readonly FallbackEntry[],
  staged: readonly StagedEntry[] | null,
): FallbackEntry[] {
  if (!staged) return [...saved]
  const byId = new Map(saved.map((entry) => [entry.id, entry]))
  const seen = new Set<number>()
  const result: FallbackEntry[] = []
  for (const item of staged) {
    const entry = byId.get(item.id)
    if (!entry || seen.has(item.id)) continue
    seen.add(item.id)
    // A model hidden since staging stays off, whatever the staged flag says.
    const on = item.enabled && !entry.hidden
    result.push({ ...entry, enabled: on, chain_enabled: on })
  }
  for (const entry of saved) {
    if (!seen.has(entry.id)) result.push(entry)
  }
  // Hidden models sit at the end of the chain, so the rest are ranked without gaps.
  return [...result.filter((entry) => !entry.hidden), ...result.filter((entry) => entry.hidden)]
}

/** The `PUT /fallback` body: dense priorities 1..N in display order. */
export function toUpdates(entries: readonly FallbackEntry[]): FallbackUpdate[] {
  return entries.map((entry, index) => ({
    model_id: entry.id,
    priority: index + 1,
    enabled: entry.enabled,
  }))
}

/** True when order or enable flags differ from the saved chain. */
export function chainChanged(
  saved: readonly FallbackEntry[],
  staged: readonly FallbackEntry[],
): boolean {
  if (saved.length !== staged.length) return true
  return saved.some((entry, index) => {
    const other = staged[index]
    return entry.id !== other.id || entry.enabled !== other.enabled
  })
}

/** Share of a window already used (0..1), or null when the window has no limit. */
export function windowFraction(window: RateWindow): number | null {
  if (window.limit === null || window.limit <= 0) return null
  return window.used / window.limit
}

export type QuotaLevel = 'ok' | 'high' | 'exhausted'

export interface QuotaSummary {
  level: QuotaLevel
  /** The tightest window, e.g. "RPD 45/50"; null when no window has a limit. */
  label: string | null
}

const WINDOW_LABELS = [
  ['rpm', 'RPM'],
  ['rpd', 'RPD'],
  ['tpm', 'TPM'],
  ['tpd', 'TPD'],
] as const

/**
 * Quota badge for a row (design.md 6.4): amber from 80% used, red when a window is exhausted.
 * The tightest limited window wins.
 */
export function quotaSummary(row: RateLimitRow | undefined): QuotaSummary {
  if (!row) return { level: 'ok', label: null }
  let best: { label: string; fraction: number } | null = null
  for (const [key, name] of WINDOW_LABELS) {
    const window = row[key]
    const fraction = windowFraction(window)
    if (fraction === null) continue
    if (best === null || fraction > best.fraction) {
      best = { label: `${name} ${window.used}/${window.limit}`, fraction }
    }
  }
  if (best === null) return { level: 'ok', label: null }
  const level: QuotaLevel = best.fraction >= 1 ? 'exhausted' : best.fraction >= 0.8 ? 'high' : 'ok'
  return { level, label: best.label }
}

/** Context window as "128K" / "1M"; "–" when unknown. */
export function formatContext(tokens: number | null | undefined): string {
  if (!tokens || tokens <= 0) return '–'
  if (tokens >= 1_000_000) return `${Number((tokens / 1_000_000).toFixed(1))}M`
  if (tokens >= 1000) return `${Math.round(tokens / 1000)}K`
  return String(tokens)
}

/** Plain-language reason a model has no usable key. */
export function blockedReason(blocked: string | null): string | null {
  switch (blocked) {
    case null:
      return null
    case 'no_keys':
      return 'No keys'
    case 'cooling_down':
      return 'Keys cooling down'
    case 'rate_limited':
      return 'Rate limit reached'
    case 'monthly_cap':
      return 'Monthly cap reached'
    default:
      return 'Unavailable'
  }
}

/** Highest used share (0..1, capped) across a row's limited windows; 0 when none is limited. */
export function quotaFraction(row: RateLimitRow | undefined): number {
  if (!row) return 0
  let best = 0
  for (const [key] of WINDOW_LABELS) {
    const fraction = windowFraction(row[key])
    if (fraction !== null && fraction > best) best = fraction
  }
  return Math.min(best, 1)
}
