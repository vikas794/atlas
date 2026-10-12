import type { Stage, Summary } from './types'

/** SHA-256 hex digest of a string's UTF-8 bytes (matches the backend's `sha256_text`). */
export async function sha256Hex(text: string): Promise<string | null> {
  const subtle = globalThis.crypto?.subtle
  if (!subtle) return null
  const bytes = new TextEncoder().encode(text)
  const digest = await subtle.digest('SHA-256', bytes)
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
}

/**
 * Whether a published video has notes that differ from what is in Drive.
 *
 * Preference order:
 * 1. the backend's own `needs_republish` flag, when it exposes one;
 * 2. otherwise the stored notes' digest vs `published_digest`, which the API
 *    already returns (`currentDigest` is SHA-256 of `summary.content`);
 * 3. otherwise unknown, which never shows the banner.
 */
export function needsRepublish(
  stage: Stage | undefined,
  summary: Pick<Summary, 'edited' | 'published_digest' | 'needs_republish'> | undefined,
  currentDigest: string | null,
): boolean {
  if (stage !== 'published' || !summary) return false
  if (typeof summary.needs_republish === 'boolean') return summary.needs_republish
  if (!summary.edited || !summary.published_digest || !currentDigest) return false
  return currentDigest !== summary.published_digest
}
