import { Monitor, Moon, Sun, type LucideIcon } from 'lucide-react'
import { useState } from 'react'
import { Link, NavLink } from 'react-router-dom'

import {
  getThemePreference,
  setThemePreference,
  THEME_PREFERENCES,
  type ThemePreference,
} from '../../lib/theme'
import { cn } from '../../lib/utils'

const THEME_OPTIONS: Record<ThemePreference, { label: string; Icon: LucideIcon }> = {
  system: { label: 'System', Icon: Monitor },
  light: { label: 'Light', Icon: Sun },
  dark: { label: 'Dark', Icon: Moon },
}

/**
 * Three-state theme control: System (default, follows the OS), Light, Dark.
 * The choice is persisted by `setThemePreference`.
 */
export function ThemeToggle() {
  const [preference, setPreference] = useState<ThemePreference>(() => getThemePreference())

  return (
    <div
      role="radiogroup"
      aria-label="Theme"
      className="inline-flex rounded-pill border border-border bg-surface p-1"
    >
      {THEME_PREFERENCES.map((option) => {
        const { label, Icon } = THEME_OPTIONS[option]
        const selected = preference === option
        return (
          <button
            key={option}
            type="button"
            role="radio"
            aria-checked={selected}
            aria-label={label}
            title={label}
            onClick={() => setPreference(setThemePreference(option))}
            className={cn(
              'inline-flex size-6 items-center justify-center rounded-pill transition',
              selected
                ? 'bg-accent-soft text-accent'
                : 'text-text-muted hover:bg-surface-muted hover:text-text',
            )}
          >
            <Icon aria-hidden="true" className="size-icon" />
          </button>
        )
      })}
    </div>
  )
}

const NAV_LINKS = [
  { to: '/models', label: 'Models' },
  { to: '/keys', label: 'Keys' },
  { to: '/diagnostics', label: 'Diagnostics' },
  { to: '/settings', label: 'Settings' },
] as const

/**
 * Floating glass pill: wordmark on the left, section links in the middle, theme toggle on the right.
 * Sticky so it stays put on the long playlist and review screens.
 */
export function TopBar() {
  return (
    <header className="sticky top-0 z-40 px-5 pt-3">
      <div className="glass mx-auto flex h-topbar w-full max-w-page items-center justify-between gap-4 rounded-pill pl-5 pr-3 shadow-card">
        <Link
          to="/"
          className="flex items-center gap-2 font-display text-section text-text"
          aria-label="Atlas home"
        >
          <span
            aria-hidden="true"
            className="inline-flex size-6 items-center justify-center rounded-lg bg-gradient-to-br from-accent to-accent-3 text-label font-semibold text-on-accent"
          >
            A
          </span>
          Atlas
        </Link>

        <div className="flex items-center gap-3">
          <nav aria-label="Main" className="flex items-center gap-1">
            {NAV_LINKS.map(({ to, label }) => (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) =>
                  cn(
                    'rounded-pill px-4 py-1 text-body font-medium transition hover:text-text',
                    isActive
                      ? 'bg-accent-soft text-accent shadow-card'
                      : 'text-text-muted hover:bg-surface-muted',
                  )
                }
              >
                {label}
              </NavLink>
            ))}
          </nav>
          <ThemeToggle />
        </div>
      </div>
    </header>
  )
}
