import { cn } from '@/lib/cn'
import type { ReactNode } from 'react'

export interface EmptyStateProps {
  title: string
  description?: ReactNode
  icon?: ReactNode
  action?: ReactNode
  className?: string
  /** `sm` for inline lists, `lg` for a whole page. */
  size?: 'sm' | 'lg'
}

/**
 * Shown when a request succeeded but returned nothing.
 *
 * Deliberately distinct from ErrorState: "nothing here yet" is a normal state
 * and must never be styled as a failure.
 */
export function EmptyState({
  title,
  description,
  icon,
  action,
  className,
  size = 'lg',
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center text-center',
        size === 'lg' ? 'rounded-[var(--radius-card)] border border-dashed border-line-strong bg-surface/60 px-6 py-12' : 'py-8',
        className,
      )}
    >
      {icon ? (
        <span
          className={cn(
            'mb-3 flex items-center justify-center rounded-full bg-brand-50 text-brand-600',
            size === 'lg' ? 'size-12' : 'size-10',
          )}
        >
          {icon}
        </span>
      ) : null}
      <p
        className={cn(
          'font-semibold text-ink-800',
          size === 'lg' ? 'text-[15px]' : 'text-sm',
        )}
      >
        {title}
      </p>
      {description ? (
        <p className="mt-1.5 max-w-sm text-sm text-ink-500">{description}</p>
      ) : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  )
}
