/** True when animation should run: a real browser that has not asked for reduced motion. */
export function motionAllowed(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  if (/jsdom/i.test(window.navigator.userAgent)) return false
  return !window.matchMedia('(prefers-reduced-motion: reduce)').matches
}
