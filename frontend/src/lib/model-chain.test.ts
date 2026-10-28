import { describe, expect, it } from 'vitest'

import {
  applyStaged,
  blockedReason,
  chainChanged,
  clampRank,
  filterEntries,
  formatContext,
  isServing,
  listedEntries,
  moveEntry,
  primaryIndex,
  quotaSummary,
  reorderServing,
  setEnabled,
  toggleMessage,
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
    hidden: false,
    key_count: 1,
    enabled_key_count: 1,
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

const off = (id: number, overrides: Partial<FallbackEntry> = {}) =>
  entry(id, { enabled: false, chain_enabled: false, ...overrides })

describe('hidden models', () => {
  const hidden = (id: number) => off(id, { hidden: true })

  it('listedEntries drops hidden models and those without a key, unless asked', () => {
    const input = [entry(1), hidden(2), entry(3, { enabled_key_count: 0 })]
    expect(ids(listedEntries(input, false))).toEqual([1])
    expect(ids(listedEntries(input, true))).toEqual([1, 2])
  })

  it('setEnabled never switches a hidden model on', () => {
    const input = [entry(1), hidden(2)]
    expect(ids(setEnabled(input, 2, true))).toEqual([1, 2])
    expect(setEnabled(input, 2, true)[1].enabled).toBe(false)
  })

  it('applyStaged moves a model hidden since staging to the end', () => {
    const staged = toStaged([entry(1), entry(2), entry(3)])
    expect(ids(applyStaged([entry(1), hidden(2), entry(3)], staged))).toEqual([1, 3, 2])
  })

  it('applyStaged keeps a model hidden since staging off', () => {
    const staged = toStaged([entry(1), entry(2)])
    const result = applyStaged([entry(1), hidden(2)], staged)
    expect(result[1]).toMatchObject({ enabled: false, chain_enabled: false })
  })
})

describe('setEnabled', () => {
  it('moves a disabled model to the end of the chain', () => {
    const input = [entry(1), entry(2), entry(3)]
    const next = setEnabled(input, 2, false)
    expect(ids(next)).toEqual([1, 3, 2])
    expect(next[2]).toMatchObject({ enabled: false, chain_enabled: false })
    expect(ids(input)).toEqual([1, 2, 3])
  })

  it('puts an enabled model right after the last enabled one', () => {
    const next = setEnabled([entry(1), entry(2), off(3), off(4)], 4, true)
    expect(ids(next)).toEqual([1, 2, 4, 3])
    expect(next[2]).toMatchObject({ enabled: true, chain_enabled: true })
  })

  it('puts an enabled model first when nothing is enabled', () => {
    expect(ids(setEnabled([off(1), off(2)], 2, true))).toEqual([2, 1])
  })

  it('counts hidden enabled models as enabled', () => {
    const hidden = entry(2, { enabled_key_count: 0 })
    expect(ids(setEnabled([entry(1), hidden, off(3), off(4)], 4, true))).toEqual([1, 2, 4, 3])
  })

  it('moves only the models whose state changes, in their current order', () => {
    const input = [entry(1), off(2), entry(3), entry(4)]
    // 2 is already disabled and stays put; 1 and 4 move last, keeping chain order.
    expect(ids(setEnabled(input, [4, 2, 1], false))).toEqual([2, 3, 1, 4])
    // Enabling 2 and 4 (already enabled) moves only 2, after the last enabled model.
    expect(ids(setEnabled([entry(1), off(2), entry(3), entry(4)], [2, 4], true))).toEqual([
      1, 3, 4, 2,
    ])
  })

  it('returns an equal chain when nothing changes, ignoring unknown ids', () => {
    const input = [entry(1), off(2)]
    expect(setEnabled(input, [1, 99], true)).toEqual(input)
    expect(setEnabled(input, 2, false)).toEqual(input)
  })

  it('leaves untouched entries of a legacy chain where they are', () => {
    // A disabled model sits between enabled ones; enabling the last one goes after enabled 3.
    expect(ids(setEnabled([entry(1), off(2), entry(3), off(4)], 4, true))).toEqual([1, 2, 3, 4])
  })

  it('serialises the moved order with dense priorities', () => {
    expect(toUpdates(setEnabled([entry(1), entry(2), entry(3)], 1, false))).toEqual([
      { model_id: 2, priority: 1, enabled: true },
      { model_id: 3, priority: 2, enabled: true },
      { model_id: 1, priority: 3, enabled: false },
    ])
  })

  it('reads as changed after off then on when the order differs from the saved one', () => {
    const saved = [entry(1), entry(2), entry(3)]
    const back = setEnabled(setEnabled(saved, 1, false), 1, true)
    expect(ids(back)).toEqual([2, 3, 1])
    expect(chainChanged(saved, back)).toBe(true)
  })
})

describe('primaryIndex', () => {
  it('is the first enabled entry, skipping a disabled one at the top', () => {
    expect(primaryIndex([off(1), entry(2), entry(3)])).toBe(1)
    expect(primaryIndex([entry(1), entry(2)])).toBe(0)
  })

  it('is -1 when nothing is enabled', () => {
    expect(primaryIndex([off(1), off(2)])).toBe(-1)
  })
})

describe('toggleMessage', () => {
  it('is empty when nothing changed', () => {
    const chain = [entry(1), entry(2)]
    expect(toggleMessage(chain, setEnabled(chain, 1, true), true)).toBe('')
  })

  it('names the model and its rank in the whole chain', () => {
    const chain = [entry(1), entry(2), entry(3)]
    expect(toggleMessage(chain, setEnabled(chain, 1, false), false)).toBe(
      'Model 1 disabled, moved to rank 3',
    )
    const off1 = setEnabled(chain, 1, false)
    expect(toggleMessage(off1, setEnabled(off1, 1, true), true)).toBe(
      'Model 1 enabled, moved to rank 3',
    )
  })

  it('falls back to the model id and counts only changed models in bulk', () => {
    const chain = [entry(1, { display_name: '' }), entry(2), off(3)]
    expect(toggleMessage(chain, setEnabled(chain, 1, false), false)).toBe(
      'model-1 disabled, moved to rank 3',
    )
    expect(toggleMessage(chain, setEnabled(chain, [1, 2, 3], false), false)).toBe(
      '2 models disabled',
    )
  })
})

describe('filterEntries', () => {
  const chain = [
    entry(1, { provider: 'gemini', display_name: 'Gemini Flash', model_id: 'flash-1' }),
    entry(2, { provider: 'nvidia', display_name: '', model_id: 'llama-70b' }),
  ]

  it('returns everything for an empty or whitespace query', () => {
    expect(ids(filterEntries(chain, ''))).toEqual([1, 2])
    expect(ids(filterEntries(chain, '   '))).toEqual([1, 2])
  })

  it('matches name, id and provider case-insensitively', () => {
    expect(ids(filterEntries(chain, 'FLASH'))).toEqual([1])
    expect(ids(filterEntries(chain, 'llama'))).toEqual([2])
    expect(ids(filterEntries(chain, ' nvid '))).toEqual([2])
  })

  it('returns an empty list when nothing matches and leaves the input alone', () => {
    expect(filterEntries(chain, 'zzz')).toEqual([])
    expect(ids(chain)).toEqual([1, 2])
  })
})

describe('staged chain', () => {
  it('serialises dense priorities in display order with the enable flags', () => {
    const chain = setEnabled([entry(7), entry(3), entry(9)], 3, false)
    expect(toUpdates(chain)).toEqual([
      { model_id: 7, priority: 1, enabled: true },
      { model_id: 9, priority: 2, enabled: true },
      { model_id: 3, priority: 3, enabled: false },
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
    // Staged order is [3, 1, 2]: model 2 was moved, then disabled, so it sits last.
    const staged = toStaged(setEnabled(moveEntry(saved, 0, 2), 2, false))

    // Model 3 was deleted elsewhere, model 4 was added, key counts changed.
    const refreshed = [entry(1), entry(2, { usable_key_count: 0 }), entry(4)]
    const shown = applyStaged(refreshed, staged)

    expect(ids(shown)).toEqual([1, 2, 4])
    expect(shown[1].enabled).toBe(false)
    expect(shown[1].usable_key_count).toBe(0)
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

describe('reorderServing', () => {
  const chain = () => [
    entry(1, { provider: 'nvidia' }),
    entry(2, { provider: 'gemini' }),
    entry(3, { provider: 'nvidia', enabled: false }),
    entry(4, { provider: 'gemini', usable_key_count: 0 }),
    entry(5, { provider: 'nvidia' }),
  ]

  it('treats only enabled models with a usable key as serving', () => {
    expect(chain().filter(isServing).map((e) => e.id)).toEqual([1, 2, 5])
  })

  it('moves a model across providers and keeps every other entry in its slot', () => {
    // serving 1,2,5 -> 5,1,2 in slots 0,1,4; disabled 3 and no-key 4 stay put
    expect(ids(reorderServing(chain(), 5, 1))).toEqual([5, 1, 3, 4, 2])
    expect(ids(reorderServing(chain(), 1, 5))).toEqual([2, 5, 3, 4, 1])
  })

  it('returns an unchanged copy for a no-op, a non-serving or an unknown id', () => {
    const input = chain()
    expect(ids(reorderServing(input, 2, 2))).toEqual([1, 2, 3, 4, 5])
    expect(ids(reorderServing(input, 3, 1))).toEqual([1, 2, 3, 4, 5])
    expect(ids(reorderServing(input, 99, 1))).toEqual([1, 2, 3, 4, 5])
  })
})
