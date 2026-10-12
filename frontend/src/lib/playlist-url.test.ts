import { describe, expect, it } from 'vitest'

import { validatePlaylistUrl } from './playlist-url'

describe('validatePlaylistUrl', () => {
  it.each([
    'https://www.youtube.com/playlist?list=PL123abc_-',
    'https://youtube.com/watch?v=abc&list=PL123abc&index=2',
    'youtube.com/playlist?list=PL123abc',
    'https://youtu.be/abc?list=PL123abc',
    '  https://m.youtube.com/playlist?list=PL123abc  ',
  ])('accepts %s', (value) => {
    expect(validatePlaylistUrl(value)).toBeNull()
  })

  it('asks for a link when empty', () => {
    expect(validatePlaylistUrl('   ')).toMatch(/paste a youtube playlist link/i)
  })

  it('rejects text that is not a link', () => {
    expect(validatePlaylistUrl('not a link')).toMatch(/does not look like a link|only youtube/i)
  })

  it('rejects other sites', () => {
    expect(validatePlaylistUrl('https://example.com/playlist?list=PL123')).toMatch(/only youtube/i)
  })

  it('rejects a YouTube video link without a list parameter', () => {
    expect(validatePlaylistUrl('https://www.youtube.com/watch?v=abc')).toMatch(/list=/)
    expect(validatePlaylistUrl('https://www.youtube.com/playlist?list=')).toMatch(/list=/)
  })
})
