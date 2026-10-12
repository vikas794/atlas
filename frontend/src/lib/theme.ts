/**
 * Theme switching for `data-theme` on `<html>`.
 *
 * Three preferences (design.md section 2): `system` (default, follows the OS),
 * `light` and `dark`. `tokens.css` defines light as the default, dark via
 * `[data-theme="dark"]`, and dark via `prefers-color-scheme` while no explicit
 * light choice is pinned. `system` therefore simply removes the attribute.
 */

export type ThemePreference = 'system' | 'light' | 'dark'
export type ResolvedTheme = 'light' | 'dark'

export const THEME_PREFERENCES: readonly ThemePreference[] = ['system', 'light', 'dark']

const STORAGE_KEY = 'atlas.theme'

function isPreference(value: unknown): value is ThemePreference {
  return value === 'system' || value === 'light' || value === 'dark'
}

/** The saved preference; `system` when never set. */
export function getThemePreference(): ThemePreference {
  if (typeof window === 'undefined') return 'system'
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    return isPreference(stored) ? stored : 'system'
  } catch {
    return 'system'
  }
}

/** The operating system preference; `light` when it cannot be determined. */
export function getSystemTheme(): ResolvedTheme {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return 'light'
  try {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

/** The theme actually in effect (a preference of `system` resolves to the OS). */
export function getResolvedTheme(preference: ThemePreference = getThemePreference()): ResolvedTheme {
  return preference === 'system' ? getSystemTheme() : preference
}

function applyPreference(preference: ThemePreference): void {
  if (typeof document === 'undefined') return
  if (preference === 'system') {
    delete document.documentElement.dataset.theme
  } else {
    document.documentElement.dataset.theme = preference
  }
}

/** Apply and persist a preference. */
export function setThemePreference(preference: ThemePreference): ThemePreference {
  applyPreference(preference)
  if (typeof window !== 'undefined') {
    try {
      if (preference === 'system') window.localStorage.removeItem(STORAGE_KEY)
      else window.localStorage.setItem(STORAGE_KEY, preference)
    } catch {
      // Storage can be unavailable (private mode, blocked cookies). The
      // attribute still applies for this session.
    }
  }
  return preference
}

/** Step system -> light -> dark -> system. */
export function nextThemePreference(current: ThemePreference): ThemePreference {
  return THEME_PREFERENCES[(THEME_PREFERENCES.indexOf(current) + 1) % THEME_PREFERENCES.length]
}

/**
 * Apply the stored preference before first paint. Call from `main.tsx`; with
 * no stored choice the attribute stays unset so the OS preference rules.
 */
export function initTheme(): ThemePreference {
  const preference = getThemePreference()
  applyPreference(preference)
  return preference
}
