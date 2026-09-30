/**
 * Typed HTTP client for the CareLoop AI backend.
 *
 * Responsibilities, and nothing else:
 *  - prepend the API base and the `/api/v1` prefix in exactly one place
 *  - attach the bearer token
 *  - turn every failure mode into ONE predictable `ApiError` shape
 *  - JSON in / JSON out, plus FormData for document upload
 *  - notify the session layer exactly once when the token is rejected
 *
 * Two rules this file exists to enforce:
 *  1. NO PHI IS EVER LOGGED. Not names, not symptoms, not document text, not
 *     error bodies that may echo patient input. Console output is limited to
 *     method, path, status and duration.
 *  2. The backend deliberately returns 404 for resources the caller may not
 *     see, so a 404 must never be rendered as "you typed the wrong id" or
 *     "this does not exist" — that would leak existence and mislead the user.
 */

export const API_PREFIX = '/api/v1'

/** Reads Vite env vars with a safe default; Vite replaces these at build time. */
function env(key: string, fallback = ''): string {
  const value = import.meta.env[key]
  return typeof value === 'string' && value.length > 0 ? value : fallback
}

/**
 * Base URL WITHOUT a trailing slash and WITHOUT `/api/v1`.
 * Empty string = same origin, which is what the dev proxy and a
 * same-origin production deployment both want.
 */
export const API_BASE_URL = env('VITE_API_BASE_URL').replace(/\/+$/, '')

export const MAX_UPLOAD_BYTES = Number(env('VITE_MAX_UPLOAD_BYTES', '20971520')) || 20971520

/** Human-readable fallback copy, keyed by status. */
const STATUS_MESSAGES: Record<number, string> = {
  400: 'The request could not be processed as sent.',
  401: 'Your session has expired. Sign in again.',
  403: 'Your care team has not granted access to this record.',
  404: 'This record is no longer available.',
  409: 'That action conflicts with the current state of this record.',
  413: 'That file is larger than the upload limit.',
  422: 'Some details need fixing before this can be saved.',
  429: 'Too many requests. Wait a moment and try again.',
  500: 'The server had a problem handling this request.',
  502: 'The document assistant is temporarily unreachable.',
  503: 'The document assistant is not available right now. Try again shortly.',
}

export interface FieldIssue {
  field: string
  message: string
}

/** The single error type every API call rejects with. */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  /** Copy that is safe and useful to show a patient. */
  readonly userMessage: string
  /** Field-level problems from a 422, if any. */
  readonly issues: FieldIssue[]
  readonly isNetworkError: boolean
  readonly traceId: string | null

  constructor(init: {
    status: number
    code: string
    userMessage: string
    issues?: FieldIssue[]
    isNetworkError?: boolean
    traceId?: string | null
  }) {
    super(init.userMessage)
    this.name = 'ApiError'
    this.status = init.status
    this.code = init.code
    this.userMessage = init.userMessage
    this.issues = init.issues ?? []
    this.isNetworkError = init.isNetworkError ?? false
    this.traceId = init.traceId ?? null
  }

  /** True when the session is gone and the user must re-authenticate. */
  get isAuthError(): boolean {
    return this.status === 401
  }

  get isForbidden(): boolean {
    return this.status === 403
  }

  get isNotFound(): boolean {
    return this.status === 404
  }

  get isConflict(): boolean {
    return this.status === 409
  }
}

/** Token read/write is injected by the session layer to avoid a circular import. */
let tokenProvider: () => string | null = () => null
let unauthorizedHandler: () => void = () => {}

export function configureApiAuth(options: {
  getToken: () => string | null
  onUnauthorized: () => void
}): void {
  tokenProvider = options.getToken
  unauthorizedHandler = options.onUnauthorized
}

type Query = Record<string, string | number | boolean | null | undefined>

/**
 * Replaces every id-shaped path segment with a placeholder for logging.
 *
 * API paths in this app are `/patients/{uuid}/medications/{uuid}`, so logging the
 * path verbatim would write patient identifiers into the browser console — a log
 * that survives screenshots, bug reports and support tickets. Only the route
 * shape is ever logged; ids are never recoverable from it.
 */
const UUID_SEGMENT = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const LONG_ID_SEGMENT = /^[0-9a-z_-]{20,}$/i

function redactPath(path: string): string {
  return path
    .split('/')
    .map((segment) => (UUID_SEGMENT.test(segment) || LONG_ID_SEGMENT.test(segment) ? ':id' : segment))
    .join('/')
}

function buildUrl(path: string, query?: Query): string {
  const suffix = path.startsWith('/') ? path : `/${path}`
  let url = `${API_BASE_URL}${API_PREFIX}${suffix}`

  if (query) {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== '') {
        params.set(key, String(value))
      }
    }
    const qs = params.toString()
    if (qs) url += `?${qs}`
  }
  return url
}

/** Pull a usable message out of FastAPI's several error envelope shapes. */
function extractMessage(payload: unknown): string | null {
  if (typeof payload === 'string') return payload.trim() || null
  if (payload && typeof payload === 'object') {
    const detail = (payload as Record<string, unknown>).detail
    if (typeof detail === 'string') return detail.trim() || null
    if (Array.isArray(detail)) return null // validation shape, handled separately
    if (detail && typeof detail === 'object') {
      const nested = (detail as Record<string, unknown>).message
      if (typeof nested === 'string') return nested.trim() || null
    }
    const message = (payload as Record<string, unknown>).message
    if (typeof message === 'string') return message.trim() || null
  }
  return null
}

/** Turn FastAPI's 422 `detail: [{loc, msg, type}]` into per-field messages. */
function extractIssues(payload: unknown): FieldIssue[] {
  if (!payload || typeof payload !== 'object') return []
  const detail = (payload as Record<string, unknown>).detail
  if (!Array.isArray(detail)) return []

  return detail.flatMap((entry): FieldIssue[] => {
    if (!entry || typeof entry !== 'object') return []
    const record = entry as Record<string, unknown>
    if (typeof record.msg !== 'string') return []
    const loc = Array.isArray(record.loc) ? record.loc : []
    // loc is [body|query|path, field, ...]; the field name is the second entry.
    const field = typeof loc[1] === 'string' ? loc[1] : loc.length > 0 ? String(loc[0]) : 'request'
    return [{ field, message: record.msg }]
  })
}

function codeForStatus(status: number): string {
  switch (status) {
    case 400:
      return 'bad_request'
    case 401:
      return 'unauthenticated'
    case 403:
      return 'forbidden'
    case 404:
      return 'not_found'
    case 409:
      return 'conflict'
    case 413:
      return 'payload_too_large'
    case 422:
      return 'validation_error'
    case 429:
      return 'rate_limited'
    case 502:
      return 'bad_gateway'
    case 503:
      return 'service_unavailable'
    default:
      return status >= 500 ? 'server_error' : 'request_failed'
  }
}

export interface RequestOptions {
  query?: Query
  /** Send as multipart/form-data (document upload). */
  formData?: FormData
  /** Send as a JSON body. `undefined` means no body at all. */
  json?: unknown
  signal?: AbortSignal
  /** Skip the bearer token, for the two public health endpoints. */
  anonymous?: boolean
}

async function request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
  const { query, formData, signal, anonymous } = options
  const url = buildUrl(path, query)
  const headers: Record<string, string> = { Accept: 'application/json' }

  if (!anonymous) {
    const token = tokenProvider()
    if (token) headers.Authorization = `Bearer ${token}`
  }
  // Deliberately NOT setting Content-Type for FormData: the browser must add the
  // multipart boundary itself. Setting it by hand is a classic upload bug.

  const { json } = options
  let body: BodyInit | undefined
  if (formData) {
    body = formData
  } else if (json !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(json)
  }

  const startedAt = Date.now()

  let response: Response
  try {
    response = await fetch(url, {
      method,
      headers,
      body,
      signal,
    })
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause
    // Method + redacted path only. See `redactPath` for why the raw path is
    // never logged: it carries patient identifiers.
    console.warn(`[careloop] ${method} ${redactPath(path)} failed: network error`)
    throw new ApiError({
      status: 0,
      code: 'network_error',
      userMessage: 'Cannot reach the CareLoop service. Check your connection and try again.',
      isNetworkError: true,
    })
  }

  const durationMs = Date.now() - startedAt

  if (!response.ok) {
    let payload: unknown = null
    try {
      payload = await response.json()
    } catch {
      payload = null
    }

    console.warn(`[careloop] ${method} ${redactPath(path)} -> ${response.status} in ${durationMs}ms`)

    const issues = extractIssues(payload)
    const serverMessage = extractMessage(payload)
    const fallback = STATUS_MESSAGES[response.status] ?? 'Something went wrong. Please try again.'

    // A 422 is the one case where the server's own message is precise and
    // field-scoped, so it is surfaced verbatim.
    const userMessage = response.status === 422 && serverMessage ? serverMessage : fallback

    if (response.status === 401 && !anonymous) unauthorizedHandler()

    throw new ApiError({
      status: response.status,
      code: codeForStatus(response.status),
      userMessage,
      issues,
      traceId: response.headers.get('x-trace-id'),
    })
  }

  if (import.meta.env.DEV) {
    console.debug(`[careloop] ${method} ${redactPath(path)} -> ${response.status} in ${durationMs}ms`)
  }

  // 204, and the "latest check-in" endpoint when there is nothing to return.
  if (response.status === 204) return null as T

  const text = await response.text()
  if (!text) return null as T

  return JSON.parse(text) as T
}

export const http = {
  get: <T>(path: string, options?: RequestOptions) => request<T>('GET', path, options),
  post: <T>(path: string, options?: RequestOptions) => request<T>('POST', path, options),
  patch: <T>(path: string, options?: RequestOptions) => request<T>('PATCH', path, options),
  delete: <T>(path: string, options?: RequestOptions) => request<T>('DELETE', path, options),
}
