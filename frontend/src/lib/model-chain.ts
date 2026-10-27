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

/** Set the enabled flag of one entry (by model id). */
export function setEnabled(
  entries: readonly FallbackEntry[],
  modelId: number,
  enabled: boolean,
): FallbackEntry[] {
  return entries.map((entry) =>
    entry.id === modelId ? { ...entry, enabled, chain_enabled: enabled } : entry,
  )
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
    result.push({ ...entry, enabled: item.enabled, chain_enabled: item.enabled })
  }
  for (const entry of saved) {
    if (!seen.has(entry.id)) result.push(entry)
  }
  return result
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
