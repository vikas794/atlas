/**
 * The one place that turns failures into sentences a person can act on.
 *
 * `request()` (api.ts) converts every failed call into an `ApiError` through
 * `toApiError` / `networkError` / `invalidResponseError`, so screens never
 * see a raw `Response`, a JSON `SyntaxError` or a bare "Failed to fetch".
 * Screens then call `errorMessage` / `failureMessage` for the text they show.
 */

/** Every failed API call throws this: `message` is for people, `code` is for branching. */
export class ApiError extends Error {
  code: string
  details: unknown
  status: number

  constructor(message: string, code: string, details: unknown, status: number) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.details = details
    this.status = status
  }
}

export const NETWORK_ERROR_MESSAGE =
  'Could not reach the Atlas backend. Check that it is running, then try again.'
export const INVALID_RESPONSE_MESSAGE =
  'The Atlas backend sent a response that could not be read. Try again; if it keeps happening, check Diagnostics.'

/** Fallback codes for responses that do not carry the backend envelope. */
function codeForStatus(status: number): string {
  if (status === 400 || status === 422) return 'validation_error'
  if (status === 401 || status === 403) return 'unauthorized'
  if (status === 404) return 'not_found'
  if (status === 409) return 'conflict'
  if (status === 413) return 'payload_too_large'
  if (status >= 500) return 'server_error'
  return 'request_failed'
}

/** Sentence for a response without a usable message (proxy pages, empty bodies). */
function messageForStatus(status: number): string {
  if (status >= 500) {
    return `The Atlas backend failed to handle the request (HTTP ${status}). Try again; if it keeps failing, check Diagnostics.`
  }
  if (status === 404) return 'The requested item was not found. It may have been deleted.'
  if (status === 409) return 'This action conflicts with the current state. Refresh and try again.'
  return `The request was rejected (HTTP ${status}).`
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function nonEmptyString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

/** First FastAPI validation problem, e.g. "Field required (url)". */
function firstValidationProblem(details: unknown): string | null {
  if (!isRecord(details) || !Array.isArray(details.errors)) return null
  const [first] = details.errors as unknown[]
  if (!isRecord(first)) return null
  const msg = nonEmptyString(first.msg)
  if (!msg) return null
  const loc = Array.isArray(first.loc)
    ? first.loc.filter((part) => part !== 'body' && part !== 'query' && part !== 'path')
    : []
  return loc.length ? `${msg} (${loc.join('.')})` : msg
}

/** Extra guidance for codes where the backend message alone does not say what to do. */
const NEXT_STEP: Record<string, string> = {
  DRIVE_AUTH_REQUIRED: 'Connect Google Drive in Settings, then try again.',
  CONFIG_MISSING: 'Check Diagnostics for the missing setting.',
}

/**
 * Turn a non-OK response into an `ApiError`. The backend envelope is
 * `{ code, message, details }`; anything else (HTML from a proxy, an empty
 * body, FastAPI's bare `{ detail }`) still yields a readable message.
 */
export async function toApiError(response: Response): Promise<ApiError> {
  const { status } = response
  let body: unknown
  try {
    const raw = await response.text()
    if (raw) body = JSON.parse(raw) as unknown
  } catch {
    body = undefined
  }

  if (!isRecord(body)) {
    return new ApiError(messageForStatus(status), codeForStatus(status), {}, status)
  }

  const code = nonEmptyString(body.code) ?? codeForStatus(status)
  const details = body.details ?? {}
  let message =
    nonEmptyString(body.message) ?? nonEmptyString(body.detail) ?? messageForStatus(status)

  const problem = firstValidationProblem(details)
  if (problem) message = `${message.replace(/\.$/, '')}: ${problem}`
  const nextStep = NEXT_STEP[code]
  if (nextStep) message = `${message.replace(/\.?$/, '.')} ${nextStep}`

  return new ApiError(message, code, details, status)
}

/** `fetch` itself rejected: the backend is down, unreachable or the request was blocked. */
export function networkError(cause: unknown): ApiError {
  return new ApiError(NETWORK_ERROR_MESSAGE, 'network_error', { cause: String(cause) }, 0)
}

/** A 2xx response whose body is not the JSON we expect. */
export function invalidResponseError(status: number): ApiError {
  return new ApiError(INVALID_RESPONSE_MESSAGE, 'invalid_response', {}, status)
}

/** The reason to show for any thrown value. */
export function errorMessage(error: unknown, fallback = 'Something went wrong.'): string {
  if (error instanceof Error && error.message.trim()) return error.message.trim()
  if (typeof error === 'string' && error.trim()) return error.trim()
  return fallback
}

/** "Could not delete the playlist: <reason>" for mutation toasts. */
export function failureMessage(action: string, error: unknown): string {
  return `${action}: ${errorMessage(error, 'unknown error.')}`
}

/** True for a 404 from the API (an answer such as "no notes yet", not a glitch). */
export function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404
}
