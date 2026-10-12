const YOUTUBE_HOSTS = new Set(['youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be'])

/**
 * Client-side check that a pasted link looks like a YouTube playlist link
 * (FR1, PRD 20: playlist links with or without extra query parameters).
 * Returns an inline error message, or `null` when the link is acceptable.
 * The server still has the final say and its errors are shown too.
 */
export function validatePlaylistUrl(input: string): string | null {
  const trimmed = input.trim()
  if (!trimmed) return 'Paste a YouTube playlist link.'

  let url: URL
  try {
    url = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`)
  } catch {
    return 'That does not look like a link.'
  }

  if (!['http:', 'https:'].includes(url.protocol) || !YOUTUBE_HOSTS.has(url.hostname.toLowerCase())) {
    return 'Only YouTube links are supported.'
  }

  const list = url.searchParams.get('list')
  if (!list || !/^[\w-]+$/.test(list)) {
    return 'This link has no playlist. Use a link that contains list=...'
  }

  return null
}
