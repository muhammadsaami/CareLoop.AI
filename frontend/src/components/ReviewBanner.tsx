import { cn } from '@/lib/cn'
import type { ReactNode } from 'react'

export interface ReviewBannerProps {
  reason?: string | null
  /** What the patient can do about it, when the backend supports an action. */
  action?: ReactNode
  /** `warning` for normal review, `danger` when an escalation is involved. */
  tone?: 'warning' | 'danger'
  title?: string
  className?: string
}

/**
 * The single most important safety component in the app.
 *
 * Phase 6 exists because an extraction or a check-in can be *uncertain*, and the
 * only acceptable UI outcome for uncertainty is that it is impossible to miss.
 * Every `needs_review` path in the backend is expected to surface one of these
 * instead of quietly rendering the value as settled fact.
 */
export function ReviewBanner({
  reason,
  action,
  tone = 'warning',
  title = 'Needs review',
  className,
}: ReviewBannerProps) {
  return (
    <div
      role="status"
      className={cn(
        'flex flex-col gap-3 rounded-[var(--radius-card)] border p-3.5 sm:flex-row sm:items-start sm:justify-between',
        tone === 'danger'
          ? 'border-danger-200 bg-danger-50'
          : 'border-warning-200 bg-warning-50',
        className,
      )}
    >
      <div className="flex min-w-0 gap-3">
        <span
          className={cn(
            'mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full',
            tone === 'danger' ? 'bg-danger-100 text-danger-700' : 'bg-warning-100 text-warning-700',
          )}
        >
          <svg
            className="size-4"
            viewBox="0 0 20 20"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M10 6.6v4M10 13.3v.2M10 2.8a7.2 7.2 0 1 1 0 14.4 7.2 7.2 0 0 1 0-14.4Z" />
          </svg>
        </span>
        <div className="min-w-0">
          <p
            className={cn(
              'text-sm font-semibold',
              tone === 'danger' ? 'text-danger-700' : 'text-warning-700',
            )}
          >
            {title}
          </p>
          <p className="mt-0.5 text-[13px] leading-relaxed text-ink-600">
            {reason?.trim() ||
              'This was extracted automatically and has not been confirmed by a person. Check it against your discharge paperwork before relying on it.'}
          </p>
        </div>
      </div>
      {action ? <div className="shrink-0 sm:pl-2">{action}</div> : null}
    </div>
  )
}
