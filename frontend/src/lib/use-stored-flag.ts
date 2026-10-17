import { useCallback, useState } from 'react'

/**
 * A boolean kept in `localStorage` (per-viewer UI preference). Storage can be
 * unavailable (private mode, blocked cookies); the flag then lives in memory only.
 */
export function useStoredFlag(key: string, initial = false): [boolean, (value: boolean) => void] {
  const [value, setValue] = useState(() => {
    try {
      const stored = window.localStorage.getItem(key)
      return stored === null ? initial : stored === '1'
    } catch {
      return initial
    }
  })

  const set = useCallback(
    (next: boolean) => {
      setValue(next)
      try {
        window.localStorage.setItem(key, next ? '1' : '0')
      } catch {
        // Not persisted; still applies for this session.
      }
    },
    [key],
  )

  return [value, set]
}
