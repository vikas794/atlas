import { describe, expect, it } from 'vitest'

import { needsRepublish, sha256Hex } from './digest'

describe('sha256Hex', () => {
  it('matches the standard SHA-256 hex digest', async () => {
    expect(await sha256Hex('abc')).toBe(
      'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
    )
  })
})

describe('needsRepublish', () => {
  const base = { edited: true, published_digest: 'aaa' }

  it('only applies to published videos', () => {
    expect(needsRepublish('approved', base, 'bbb')).toBe(false)
    expect(needsRepublish(undefined, base, 'bbb')).toBe(false)
  })

  it('prefers the backend needs_republish flag', () => {
    expect(needsRepublish('published', { ...base, needs_republish: true }, 'aaa')).toBe(true)
    expect(needsRepublish('published', { ...base, needs_republish: false }, 'bbb')).toBe(false)
  })

  it('compares digests when the flag is missing', () => {
    expect(needsRepublish('published', base, 'bbb')).toBe(true)
    expect(needsRepublish('published', base, 'aaa')).toBe(false)
  })

  it('does not show the banner for edited-but-in-sync notes (after Re-publish)', () => {
    expect(needsRepublish('published', { edited: true, published_digest: 'ccc' }, 'ccc')).toBe(
      false,
    )
  })

  it('stays hidden when the digest cannot be computed', () => {
    expect(needsRepublish('published', base, null)).toBe(false)
  })
})
