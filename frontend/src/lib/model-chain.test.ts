import { describe, expect, it } from 'vitest'

import {
  applyStaged,
  blockedReason,
  chainChanged,
  clampRank,
  formatContext,
  moveEntry,
  quotaSummary,
  setEnabled,
  toStaged,
  toUpdates,
} from './model-chain'
import type { FallbackEntry, RateLimitRow } from './types'

function entry(id: number, overrides: Partial<FallbackEntry> = {}): FallbackEntry {
  return {
    id,
    provider: 'gemini',
    model_id: `model-${id}`,
    display_name: `Model ${id}`,
    litellm_model: `gemini/model-${id}`,
    api_base: null,
    input_token_limit: 128_000,
    request_timeout_seconds: null,
    rpm_limit: null,
    rpd_limit: null,
    tpm_limit: null,
    tpd_limit: null,
    enabled: true,
    source: 'user',
    priority: id,
    chain_enabled: true,
    key_count: 1,
    usable_key_count: 1,
    blocked: null,
    ...overrides,
  }
}

const ids = (entries: FallbackEntry[]) => entries.map((e) => e.id)

describe('moveEntry', () => {
  it('moves an entry and leaves the input untouched', () => {
    const input = [entry(1), entry(2), entry(3)]
    expect(ids(moveEntry(input, 0, 2))).toEqual([2, 3, 1])
    expect(ids(input)).toEqual([1, 2, 3])
  })

  it('clamps an out-of-range target and ignores an invalid source', () => {
    const input = [entry(1), entry(2), entry(3)]
    expect(ids(moveEntry(input, 2, -5))).toEqual([3, 1, 2])
    expect(ids(moveEntry(input, 0, 99))).toEqual([2, 3, 1])
    expect(ids(moveEntry(input, 9, 0))).toEqual([1, 2, 3])
  })
})

describe('clampRank', () => {
  it('keeps an index inside the list', () => {
    expect(clampRank(-1, 4)).toBe(0)
    expect(clampRank(2.7, 4)).toBe(2)
    expect(clampRank(10, 4)).toBe(3)
    expect(clampRank(5, 0)).toBe(0)
  })
})

describe('staged chain', () => {
  it('serialises dense priorities in display order with the enable flags', () => {
    const chain = setEnabled([entry(7), entry(3), entry(9)], 3, false)
    expect(toUpdates(chain)).toEqual([
      { model_id: 7, priority: 1, enabled: true },
      { model_id: 3, priority: 2, enabled: false },
      { model_id: 9, priority: 3, enabled: true },
    ])
  })

  it('detects order and enable changes only', () => {
    const saved = [entry(1), entry(2)]
    expect(chainChanged(saved, [entry(1), entry(2)])).toBe(false)
    expect(chainChanged(saved, [entry(2), entry(1)])).toBe(true)
    expect(chainChanged(saved, setEnabled(saved, 2, false))).toBe(true)
    expect(chainChanged(saved, [entry(1)])).toBe(true)
  })

  it('keeps staged edits when the saved list changes underneath', () => {
    const saved = [entry(1), entry(2), entry(3)]
    const staged = toStaged(setEnabled(moveEntry(saved, 0, 2), 2, false))

    // Model 3 was deleted elsewhere, model 4 was added, key counts changed.
    const refreshed = [entry(1), entry(2, { usable_key_count: 0 }), entry(4)]
    const shown = applyStaged(refreshed, staged)

    expect(ids(shown)).toEqual([2, 1, 4])
    expect(shown[0].enabled).toBe(false)
    expect(shown[0].usable_key_count).toBe(0)
  })

  it('shows the saved chain untouched when nothing is staged', () => {
    const saved = [entry(1), entry(2)]
    expect(applyStaged(saved, null)).toEqual(saved)
  })
})

describe('quotaSummary', () => {
  const row = (rpm: [number, number | null], rpd: [number, number | null]): RateLimitRow => ({
    model_pk: 1,
    rpm: { used: rpm[0], limit: rpm[1] },
    rpd: { used: rpd[0], limit: rpd[1] },
    tpm: { used: 0, limit: null },
    tpd: { used: 0, limit: null },
  })

  it('has no label when no window is limited', () => {
    expect(quotaSummary(undefined)).toEqual({ level: 'ok', label: null })
    expect(quotaSummary(row([5, null], [9, null]))).toEqual({ level: 'ok', label: null })
  })

  it('turns amber from 80 percent and red when exhausted, naming the tightest window', () => {
    expect(quotaSummary(row([1, 10], [10, 100]))).toEqual({ level: 'ok', label: 'RPM 1/10' })
    expect(quotaSummary(row([1, 10], [80, 100]))).toEqual({ level: 'high', label: 'RPD 80/100' })
    expect(quotaSummary(row([10, 10], [20, 100]))).toEqual({
      level: 'exhausted',
      label: 'RPM 10/10',
    })
  })
})

describe('formatters', () => {
  it('abbreviates the context window', () => {
    expect(formatContext(128_000)).toBe('128K')
    expect(formatContext(1_000_000)).toBe('1M')
    expect(formatContext(1_500_000)).toBe('1.5M')
    expect(formatContext(null)).toBe('–')
  })

  it('words the reason a model is blocked', () => {
    expect(blockedReason(null)).toBeNull()
    expect(blockedReason('no_keys')).toBe('No keys')
    expect(blockedReason('cooling_down')).toBe('Keys cooling down')
    expect(blockedReason('something_new')).toBe('Unavailable')
  })
})
