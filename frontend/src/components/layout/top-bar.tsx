import { Monitor, Moon, Sun, type LucideIcon } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'

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
      className="inline-flex rounded border border-border bg-surface p-1"
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
              'inline-flex size-6 items-center justify-center rounded transition',
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

/**
 * Section 5: 56px top bar, app name on the left, theme toggle on the right,
 * sticky so it stays put on the long playlist and review screens.
 */
export function TopBar() {
  return (
    <header className="sticky top-0 z-40 h-topbar border-b border-border bg-surface">
      <div className="mx-auto flex h-topbar w-full max-w-page items-center justify-between px-5">
        <Link to="/" className="text-section text-text" aria-label="Atlas home">
          Atlas
        </Link>

        <div className="flex items-center gap-3">
          <Link
            to="/settings"
            className="rounded px-2 text-label text-text-muted transition hover:bg-surface-muted hover:text-text"
          >
            Settings
          </Link>
          <ThemeToggle />
        </div>
      </div>
    </header>
  )
}
