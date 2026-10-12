import { describe, expect, it } from 'vitest'

import {
  formatCountdown,
  formatExactTime,
  formatPosition,
  formatRelativeTime,
  isFuture,
  toDateTimeAttr,
} from './format'

const NOW = Date.parse('2026-10-10T12:00:00Z')
const ago = (seconds: number) => new Date(NOW - seconds * 1000).toISOString()

describe('formatRelativeTime', () => {
  it.each([
    [10, 'just now'],
    [180, '3 min ago'],
    [3600 * 2, '2 h ago'],
    [86400 * 5, '5 d ago'],
  ])('formats %is as %s', (seconds, expected) => {
    expect(formatRelativeTime(ago(seconds), NOW)).toBe(expected)
  })

  it('formats the future', () => {
    expect(formatRelativeTime(ago(-300), NOW)).toBe('in 5 min')
  })

  it('treats zone-less backend stamps as UTC', () => {
    expect(formatRelativeTime('2026-10-10T11:57:00', NOW)).toBe('3 min ago')
  })

  it('returns an empty string for missing or invalid input', () => {
    expect(formatRelativeTime(null, NOW)).toBe('')
    expect(formatRelativeTime('nonsense', NOW)).toBe('')
  })
})

describe('exact time helpers', () => {
  it('gives a tooltip string and a dateTime attribute', () => {
    expect(formatExactTime('2026-10-10T11:57:00Z')).not.toBe('')
    expect(toDateTimeAttr('2026-10-10T11:57:00Z')).toBe('2026-10-10T11:57:00.000Z')
    expect(toDateTimeAttr(null)).toBeUndefined()
  })
})

describe('formatCountdown', () => {
  it('formats seconds, minutes and hours', () => {
    expect(formatCountdown(ago(-30), NOW)).toBe('30s')
    expect(formatCountdown(ago(-120), NOW)).toBe('2m')
    expect(formatCountdown(ago(-3900), NOW)).toBe('1h 5m')
  })

  it('says now when due and detects the future', () => {
    expect(formatCountdown(ago(5), NOW)).toBe('now')
    expect(isFuture(ago(-5), NOW)).toBe(true)
    expect(isFuture(ago(5), NOW)).toBe(false)
  })
})

describe('formatPosition', () => {
  it('zero-pads 1-based positions', () => {
    expect(formatPosition(1)).toBe('001')
    expect(formatPosition(12)).toBe('012')
    expect(formatPosition(1234)).toBe('1234')
  })
})
