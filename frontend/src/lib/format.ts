/** Time and number formatting shared by every screen (design.md sections 6.2, 8). */

const MINUTE = 60
const HOUR = 3600
const DAY = 86400

/** Backend stamps are UTC; treat a zone-less ISO string as UTC, not local time. */
function parseTimestamp(iso: string | null | undefined): number | null {
  if (!iso) return null
  const hasZone = /(Z|[+-]\d{2}:?\d{2})$/i.test(iso)
  const normalised = hasZone ? iso : `${iso.replace(' ', 'T')}Z`
  const ms = Date.parse(normalised)
  return Number.isNaN(ms) ? null : ms
}

/**
 * "3 min ago", "2 h ago", "in 5 min". Returns '' for missing or invalid input
 * so callers can render a dash. `now` is injectable for deterministic tests.
 */
export function formatRelativeTime(
  iso: string | null | undefined,
  now: number = Date.now(),
): string {
  const ms = parseTimestamp(iso)
  if (ms === null) return ''

  const diff = Math.round((now - ms) / 1000)
  const abs = Math.abs(diff)
  if (abs < 45) return 'just now'

  let amount: string
  if (abs < HOUR) amount = `${Math.max(1, Math.round(abs / MINUTE))} min`
  else if (abs < DAY) amount = `${Math.round(abs / HOUR)} h`
  else if (abs < 30 * DAY) amount = `${Math.round(abs / DAY)} d`
  else return new Date(ms).toLocaleDateString()

  return diff >= 0 ? `${amount} ago` : `in ${amount}`
}

/** Exact local date and time, for the tooltip next to a relative time. */
export function formatExactTime(iso: string | null | undefined): string {
  const ms = parseTimestamp(iso)
  return ms === null ? '' : new Date(ms).toLocaleString()
}

/** Machine-readable value for the `<time dateTime>` attribute. */
export function toDateTimeAttr(iso: string | null | undefined): string | undefined {
  const ms = parseTimestamp(iso)
  return ms === null ? undefined : new Date(ms).toISOString()
}

/** Short countdown for a scheduled retry: "45s", "2m", "1h 5m"; "now" when due. */
export function formatCountdown(iso: string | null | undefined, now: number = Date.now()): string {
  const ms = parseTimestamp(iso)
  if (ms === null) return ''
  const seconds = Math.ceil((ms - now) / 1000)
  if (seconds <= 0) return 'now'
  if (seconds < MINUTE) return `${seconds}s`
  if (seconds < HOUR) return `${Math.ceil(seconds / MINUTE)}m`
  const hours = Math.floor(seconds / HOUR)
  const minutes = Math.ceil((seconds % HOUR) / MINUTE)
  return minutes === 60 ? `${hours + 1}h` : minutes ? `${hours}h ${minutes}m` : `${hours}h`
}

/** True when the timestamp is in the future relative to `now`. */
export function isFuture(iso: string | null | undefined, now: number = Date.now()): boolean {
  const ms = parseTimestamp(iso)
  return ms !== null && ms > now
}

/** Playlist positions are 1-based; display them zero-padded ("001"). */
export function formatPosition(position: number, width = 3): string {
  return String(position).padStart(width, '0')
}

/** "1 video", "187 videos". */
export function pluralize(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`
}
