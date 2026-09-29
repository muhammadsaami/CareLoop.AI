import { Button } from './Button'
import { cn } from '@/lib/cn'
import { ApiError } from '@/lib/apiClient'

export interface ErrorStateProps {
  error: unknown
  /** Overrides the derived message. */
  title?: string
  onRetry?: () => void
  retryLabel?: string
  className?: string
  compact?: boolean
}

/**
 * Turns any thrown value into copy a patient can act on.
 *
 * Copy rules baked in here:
 *  - never reveal whether a record exists behind a 404
 *  - never echo a raw server string that might contain patient-entered text
 *  - a trace id is shown only when the backend supplied one, and only on 5xx
 */
export function ErrorState({
  error,
  title,
  onRetry,
  retryLabel = 'Try again',
  className,
  compact = false,
}: ErrorStateProps) {
  const apiError = error instanceof ApiError ? error : null
  const message = apiError?.userMessage ?? 'Something went wrong. Please try again.'
  const canRetry = Boolean(onRetry) && (apiError?.isNetworkError || !apiError || apiError.status >= 500 || apiError.status === 429)

  if (compact) {
    return (
      <div
        role="alert"
        className={cn(
          'flex flex-col gap-2 rounded-[var(--radius-card)] border border-danger-200 bg-danger-50 p-3.5 sm:flex-row sm:items-center sm:justify-between',
          className,
        )}
      >
        <div className="min-w-0">
          <p className="text-sm font-medium text-danger-700">{title ?? 'Could not load this'}</p>
          <p className="mt-0.5 text-[13px] text-ink-600">{message}</p>
        </div>
        {canRetry ? (
          <Button variant="secondary" size="sm" onClick={onRetry} className="shrink-0">
            {retryLabel}
          </Button>
        ) : null}
      </div>
    )
  }

  return (
    <div
      role="alert"
      className={cn(
        'flex flex-col items-center justify-center rounded-[var(--radius-card)] border border-danger-200 bg-danger-50/60 px-6 py-12 text-center',
        className,
      )}
    >
      <span className="mb-3 flex size-12 items-center justify-center rounded-full bg-danger-100 text-danger-600">
        <svg
          className="size-6"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinecap="round"
          aria-hidden="true"
        >
          <path d="M12 8.5v4.2M12 15.6v.3M12 3.2a8.8 8.8 0 1 1 0 17.6 8.8 8.8 0 0 1 0-17.6Z" />
        </svg>
      </span>
      <p className="text-[15px] font-semibold text-danger-700">
        {title ?? (apiError?.status === 403 ? 'Not available for this account' : 'Something went wrong')}
      </p>
      <p className="mt-1.5 max-w-sm text-sm text-ink-600">{message}</p>
      {apiError?.traceId && apiError.status >= 500 ? (
        <p className="mt-2 font-mono text-[11px] text-ink-400">Reference: {apiError.traceId}</p>
      ) : null}
      {canRetry ? (
        <Button variant="secondary" className="mt-5" onClick={onRetry}>
          {retryLabel}
        </Button>
      ) : null}
      <p className="mt-5 max-w-sm text-[12px] text-ink-400">
        If this keeps happening, contact your care team. Please do not send clinical details by
        message.
      </p>
    </div>
  )
}

/** Inline, single-line error used inside forms and cards. */
export function InlineError({ message, className }: { message: string; className?: string }) {
  return (
    <p role="alert" className={cn('text-[13px] font-medium text-danger-700', className)}>
      {message}
    </p>
  )
}

// `errorMessage` lives beside the component that renders the error, because
// every screen imports both to handle a failure.
// oxlint-disable-next-line react/only-export-components
export function errorMessage(error: unknown, fallback = 'Something went wrong. Please try again.'): string {
  return error instanceof ApiError ? error.userMessage : fallback
}
