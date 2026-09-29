import { cn } from '@/lib/cn'

export interface LoadingStateProps {
  label?: string
  /** `block` fills a card, `inline` sits inside a line of text. */
  variant?: 'block' | 'inline' | 'page'
  className?: string
}

function Skeleton({ className }: { className?: string }) {
  return <div className={cn('animate-pulse rounded-md bg-ink-100', className)} />
}

/** Consistent loading affordance. Announces itself politely, never assertively. */
export function LoadingState({ label = 'Loading', variant = 'block', className }: LoadingStateProps) {
  if (variant === 'inline') {
    return (
      <span role="status" className={cn('inline-flex items-center gap-2 text-sm text-ink-500', className)}>
        <svg className="size-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2.5" opacity="0.25" />
          <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
        </svg>
        <span className="sr-only">{label}</span>
      </span>
    )
  }

  if (variant === 'page') {
    return (
      <div role="status" aria-live="polite" className={cn('space-y-4', className)}>
        <span className="sr-only">{label}</span>
        <Skeleton className="h-8 w-48" />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }, (_, index) => (
            <Skeleton key={index} className="h-24" />
          ))}
        </div>
        <Skeleton className="h-64" />
      </div>
    )
  }

  return (
    <div role="status" aria-live="polite" className={cn('space-y-3', className)}>
      <span className="sr-only">{label}</span>
      {Array.from({ length: 3 }, (_, index) => (
        <div key={index} className="rounded-[var(--radius-card)] border border-line bg-surface p-4">
          <Skeleton className="h-4 w-1/3" />
          <Skeleton className="mt-2.5 h-3 w-2/3" />
          <Skeleton className="mt-2 h-3 w-1/2" />
        </div>
      ))}
    </div>
  )
}
