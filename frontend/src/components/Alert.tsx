import { cn } from '@/lib/cn'
import type { ReactNode } from 'react'

export type AlertTone = 'info' | 'success' | 'warning' | 'danger' | 'brand'

const TONES: Record<AlertTone, { wrap: string; icon: string; title: string }> = {
  info: {
    wrap: 'bg-info-50 border-info-200 text-info-700',
    icon: 'text-info-600',
    title: 'text-info-700',
  },
  success: {
    wrap: 'bg-success-50 border-success-200 text-success-900',
    icon: 'text-success-600',
    title: 'text-success-700',
  },
  warning: {
    wrap: 'bg-warning-50 border-warning-200 text-warning-900',
    icon: 'text-warning-600',
    title: 'text-warning-700',
  },
  danger: {
    wrap: 'bg-danger-50 border-danger-200 text-danger-900',
    icon: 'text-danger-600',
    title: 'text-danger-700',
  },
  brand: {
    wrap: 'bg-brand-50 border-brand-200 text-brand-900',
    icon: 'text-brand-600',
    title: 'text-brand-800',
  },
}

const ICONS: Record<AlertTone, ReactNode> = {
  info: (
    <path d="M10 6.5v5M10 14.2v.3M10 2.8a7.2 7.2 0 1 1 0 14.4 7.2 7.2 0 0 1 0-14.4Z" />
  ),
  success: (
    <path d="m6.5 10.2 2.4 2.4 4.6-5M10 2.8a7.2 7.2 0 1 1 0 14.4 7.2 7.2 0 0 1 0-14.4Z" />
  ),
  warning: (
    <path d="M10 6.5v4.2M10 13.4v.3M8.3 3.3 2.6 13.1a1.5 1.5 0 0 0 1.3 2.2h12.2a1.5 1.5 0 0 0 1.3-2.2L11.7 3.3a1.5 1.5 0 0 0-2.6 0Z" />
  ),
  danger: (
    <path d="M10 6.8v3.9M10 13.3v.3M10 2.8a7.2 7.2 0 1 1 0 14.4 7.2 7.2 0 0 1 0-14.4Z" />
  ),
  brand: (
    <path d="M10 8.4v5M10 6.6v.2M10 17.2a7.2 7.2 0 1 1 0-14.4 7.2 7.2 0 0 1 0 14.4Z" />
  ),
}

export interface AlertProps {
  tone?: AlertTone
  title?: ReactNode
  children?: ReactNode
  /** Rendered at the top-right, e.g. a dismiss button. */
  action?: ReactNode
  className?: string
}

/**
 * Inline status message.
 *
 * `role="alert"` is deliberate: extraction failures, escalation notices and
 * validation problems are not decoration, and a screen reader must announce
 * them when they appear.
 */
export function Alert({ tone = 'info', title, children, action, className }: AlertProps) {
  const styles = TONES[tone]
  return (
    <div
      role="alert"
      className={cn('flex gap-3 rounded-[var(--radius-card)] border p-3.5', styles.wrap, className)}
    >
      <svg
        className={cn('mt-0.5 size-4 shrink-0', styles.icon)}
        viewBox="0 0 20 20"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        {ICONS[tone]}
      </svg>
      <div className="min-w-0 flex-1 text-sm">
        {title ? <p className={cn('font-semibold', styles.title)}>{title}</p> : null}
        {children ? <div className={cn(title && 'mt-0.5', 'text-ink-700')}>{children}</div> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  )
}
