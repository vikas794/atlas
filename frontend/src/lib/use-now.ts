import { useSyncExternalStore } from 'react'

/**
 * One shared 30s ticker so every relative time on screen ("3 min ago") stays
 * fresh without each `<RelativeTime>` owning a timer.
 */
const TICK_MS = 30_000
let current = Date.now()
let timer: ReturnType<typeof setInterval> | undefined
const listeners = new Set<() => void>()

function subscribe(listener: () => void) {
  listeners.add(listener)
  if (timer === undefined) {
    current = Date.now()
    timer = setInterval(() => {
      current = Date.now()
      listeners.forEach((notify) => notify())
    }, TICK_MS)
  }
  return () => {
    listeners.delete(listener)
    if (listeners.size === 0 && timer !== undefined) {
      clearInterval(timer)
      timer = undefined
    }
  }
}

/** Current time in ms, refreshed every 30 seconds. */
export function useNow(): number {
  return useSyncExternalStore(
    subscribe,
    () => current,
    () => Date.now(),
  )
}
