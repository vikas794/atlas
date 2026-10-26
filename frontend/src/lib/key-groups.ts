import type { LlmKey } from './types'

export interface KeyGroup {
  provider: string
  label: string
  keys: LlmKey[]
}

/** Group keys under their provider; providers with keys come in the order the API listed them. */
export function groupKeys(keys: readonly LlmKey[], labels: Map<string, string>): KeyGroup[] {
  const groups = new Map<string, KeyGroup>()
  for (const key of keys) {
    const group = groups.get(key.provider) ?? {
      provider: key.provider,
      label: labels.get(key.provider) ?? key.provider,
      keys: [],
    }
    group.keys.push(key)
    groups.set(key.provider, group)
  }
  return [...groups.values()]
}
