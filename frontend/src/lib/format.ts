/**
 * Presentation-only formatting.
 *
 * Clinical copy rule: these helpers may prettify a value, but they must never
 * round, upgrade, or otherwise strengthen it. `formatBytes` can say "2.1 MB",
 * it must never say "2 MB" for a 2.1 MB file when the limit is a safety
 * boundary. Anything a care decision depends on is shown verbatim.
 */

/** ISO string -> "12 Mar 2026". Unparseable input is returned unchanged. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

/** ISO string -> "12 Mar 2026, 14:05". */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** ISO string -> "14:05". */
export function formatTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

/** "in 3 days" / "2 hours ago" / "today". Never implies medical urgency. */
export function formatRelative(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value

  const diffMs = date.getTime() - Date.now()
  const absMs = Math.abs(diffMs)
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })

  if (absMs < 60_000) return 'just now'
  if (absMs < 3_600_000) return rtf.format(Math.round(diffMs / 60_000), 'minute')
  if (absMs < 86_400_000) return rtf.format(Math.round(diffMs / 3_600_000), 'hour')
  if (absMs < 2_592_000_000) return rtf.format(Math.round(diffMs / 86_400_000), 'day')
  return formatDate(value)
}

/** Bytes -> "1.4 MB". One decimal above 1 KB; exact bytes below 1 KB. */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return '—'
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB']
  let value = bytes / 1024
  let unitIndex = 0
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024
    unitIndex += 1
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`
}

/** Retries 429/503 responses using the server's Retry-After hint when present. */
export function parseRetryAfter(header: string | null): number | null {
  if (!header) return null
  const seconds = Number(header)
  if (Number.isFinite(seconds) && seconds > 0) return seconds * 1000
  const date = Date.parse(header)
  return Number.isNaN(date) ? null : Math.max(0, date - Date.now())
}

/** Today's date as YYYY-MM-DD in the patient's own timezone if we can resolve it. */
export function todayIso(timezone?: string | null): string {
  try {
    const formatter = new Intl.DateTimeFormat('en-CA', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      timeZone: timezone ?? undefined,
    })
    return formatter.format(new Date())
  } catch {
    return new Date().toISOString().slice(0, 10)
  }
}

/**
 * ISO datetime -> "YYYY-MM-DD" in a specific timezone (host zone when omitted).
 *
 * Same contract as `todayIso` but for arbitrary timestamps, so a stored
 * `scheduled_time` can be compared against "today" in the patient's zone rather
 * than the host's. Unparseable input is returned unchanged so a caller never
 * guesses a date for a value it cannot understand.
 */
export function dateIsoInZone(value: string, timezone?: string | null): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  try {
    const formatter = new Intl.DateTimeFormat('en-CA', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      timeZone: timezone ?? undefined,
    })
    return formatter.format(date)
  } catch {
    return date.toISOString().slice(0, 10)
  }
}

/** "08:00" style local time, validated before it is sent to the backend. */
export function isValidLocalTime(value: string): boolean {
  return /^([01]\d|2[0-3]):[0-5]\d$/.test(value)
}

/** Truncate for a card preview without cutting mid-word where avoidable. */
export function truncate(value: string, max: number): string {
  if (value.length <= max) return value
  const slice = value.slice(0, max)
  const lastSpace = slice.lastIndexOf(' ')
  return `${lastSpace > max * 0.6 ? slice.slice(0, lastSpace) : slice}…`
}

/** Display an unknown enum member without crashing or silently dropping it. */
export function humanizeEnum(
  value: string | null | undefined,
  labels?: Readonly<Record<string, string>>,
): string {
  if (!value) return '—'
  if (labels?.[value]) return labels[value]
  return value
    .split('_')
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}
