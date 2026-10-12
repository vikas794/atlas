import { describe, expect, it } from 'vitest'

import {
  getResolvedTheme,
  getThemePreference,
  initTheme,
  nextThemePreference,
  setThemePreference,
} from './theme'

describe('theme preference', () => {
  it('defaults to system with no data-theme attribute', () => {
    expect(getThemePreference()).toBe('system')
    expect(initTheme()).toBe('system')
    expect(document.documentElement.dataset.theme).toBeUndefined()
  })

  it('writes data-theme for light and dark and persists the choice', () => {
    setThemePreference('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(window.localStorage.getItem('atlas.theme')).toBe('dark')
    expect(getResolvedTheme()).toBe('dark')

    setThemePreference('light')
    expect(document.documentElement.dataset.theme).toBe('light')
    expect(window.localStorage.getItem('atlas.theme')).toBe('light')
  })

  it('system clears the attribute and the stored value', () => {
    setThemePreference('dark')
    setThemePreference('system')
    expect(document.documentElement.dataset.theme).toBeUndefined()
    expect(window.localStorage.getItem('atlas.theme')).toBeNull()
    expect(getThemePreference()).toBe('system')
  })

  it('restores the stored preference on init', () => {
    window.localStorage.setItem('atlas.theme', 'dark')
    expect(initTheme()).toBe('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
  })

  it('cycles system -> light -> dark -> system', () => {
    expect(nextThemePreference('system')).toBe('light')
    expect(nextThemePreference('light')).toBe('dark')
    expect(nextThemePreference('dark')).toBe('system')
  })
})
