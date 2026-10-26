import { describe, expect, it } from 'vitest'

import {
  activeCooldowns,
  autoDisabledNote,
  keyBadge,
  keyStatusToBadge,
  longestCooldown,
} from './key-status-badge'
import type { KeyStatus } from './types'

const NOW = 1_800_000_000_000

describe('keyStatusToBadge', () => {
  it('gives every status an icon, a label and a tone', () => {
    const statuses: KeyStatus[] = ['healthy', 'rate_limited', 'invalid', 'error', 'unknown']
    const specs = statuses.map((status) => keyStatusToBadge(status))

    expect(specs.map((spec) => spec.tone)).toEqual(['success', 'warn', 'error', 'error', 'neutral'])
    for (const spec of specs) {
      expect(spec.label).toBeTruthy()
      expect(spec.icon).toBeTruthy()
    }
  })

  it('shows a usable key with an active cooldown as cooling down', () => {
    expect(keyStatusToBadge('healthy', true).label).toBe('Cooling down')
    expect(keyStatusToBadge('unknown', true).label).toBe('Cooling down')
  })

  it('keeps a failing status visible even while a cooldown is active', () => {
    expect(keyStatusToBadge('invalid', true).label).toBe('Invalid')
    expect(keyStatusToBadge('error', true).label).toBe('Error')
  })

  it('falls back to unchecked for a status it does not know', () => {
    expect(keyStatusToBadge('weird' as KeyStatus).label).toBe('Unchecked')
  })
})

describe('cooldowns', () => {
  const cooldowns = [
    { model_id: '1', expires_at: NOW / 1000 + 60, source: 'heuristic' },
    { model_id: '*', expires_at: NOW / 1000 + 600, source: 'credit' },
    { model_id: '2', expires_at: NOW / 1000 - 5, source: 'heuristic' },
  ]

  it('ignores expired cooldowns and picks the one that ends last', () => {
    expect(activeCooldowns(cooldowns, NOW)).toHaveLength(2)
    expect(longestCooldown(cooldowns, NOW)?.model_id).toBe('*')
    expect(longestCooldown([], NOW)).toBeNull()
  })

  it('badges a key from its status and cooldowns', () => {
    expect(keyBadge({ status: 'healthy', cooldowns }, NOW).label).toBe('Cooling down')
    expect(keyBadge({ status: 'healthy', cooldowns: [] }, NOW).label).toBe('Healthy')
  })
})

describe('autoDisabledNote', () => {
  it('only speaks for auto-disabled keys', () => {
    expect(autoDisabledNote({ auto_disabled: false, consecutive_failures: 5 })).toBeNull()
    expect(autoDisabledNote({ auto_disabled: true, consecutive_failures: 3 })).toBe(
      'Disabled after 3 failed checks',
    )
  })
})
