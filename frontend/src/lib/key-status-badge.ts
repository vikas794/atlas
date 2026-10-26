import type { BadgeSpec } from './stage-badge'
import type { KeyCooldown, KeyStatus, LlmKey } from './types'

/**
 * The single source of truth for API-key status -> badge (design.md 6.5, 9), next to
 * `stageToBadge`. Status is never conveyed by colour alone: every spec carries an icon and a label.
 */

const BY_STATUS: Record<KeyStatus, BadgeSpec> = {
  healthy: { label: 'Healthy', tone: 'success', icon: 'check', inProgress: false },
  rate_limited: { label: 'Rate limited', tone: 'warn', icon: 'clock', inProgress: false },
  invalid: { label: 'Invalid', tone: 'error', icon: 'x-circle', inProgress: false },
  error: { label: 'Error', tone: 'error', icon: 'alert', inProgress: false },
  unknown: { label: 'Unchecked', tone: 'neutral', icon: 'help-circle', inProgress: false },
}

const COOLING_DOWN: BadgeSpec = {
  label: 'Cooling down',
  tone: 'warn',
  icon: 'timer',
  inProgress: false,
}

/**
 * Badge for a key. A key whose stored status is still usable (healthy or unchecked) but that has an
 * active cooldown is shown as cooling down, since the router will skip it until the cooldown ends.
 */
export function keyStatusToBadge(status: KeyStatus, coolingDown = false): BadgeSpec {
  if (coolingDown && (status === 'healthy' || status === 'unknown')) return COOLING_DOWN
  return BY_STATUS[status] ?? BY_STATUS.unknown
}

/** Cooldowns that have not expired yet at `nowMs`. */
export function activeCooldowns(
  cooldowns: readonly KeyCooldown[],
  nowMs: number = Date.now(),
): KeyCooldown[] {
  return cooldowns.filter((cooldown) => cooldown.expires_at * 1000 > nowMs)
}

/** The latest-ending active cooldown (what the user waits for), or null. */
export function longestCooldown(
  cooldowns: readonly KeyCooldown[],
  nowMs: number = Date.now(),
): KeyCooldown | null {
  const active = activeCooldowns(cooldowns, nowMs)
  if (active.length === 0) return null
  return active.reduce((latest, next) => (next.expires_at > latest.expires_at ? next : latest))
}

/** Badge for a key row, folding in its cooldowns. */
export function keyBadge(key: Pick<LlmKey, 'status' | 'cooldowns'>, nowMs?: number): BadgeSpec {
  return keyStatusToBadge(key.status, activeCooldowns(key.cooldowns, nowMs).length > 0)
}

/** "Disabled after 3 failed checks" for a key the health check switched off. */
export function autoDisabledNote(key: Pick<LlmKey, 'auto_disabled' | 'consecutive_failures'>) {
  if (!key.auto_disabled) return null
  return `Disabled after ${Math.max(key.consecutive_failures, 3)} failed checks`
}
