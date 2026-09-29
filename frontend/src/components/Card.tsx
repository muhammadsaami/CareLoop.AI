import { cn } from '@/lib/cn'
import type { ReactNode } from 'react'

export type CardPadding = 'none' | 'sm' | 'md' | 'lg'

const PADDING: Record<CardPadding, string> = {
  none: '',
  sm: 'p-3',
  md: 'p-4 sm:p-5',
  lg: 'p-5 sm:p-6',
}

export interface CardProps {
  children: ReactNode
  className?: string
  padding?: CardPadding
  /** Removes the hover lift, for cards that are not interactive. */
  flat?: boolean
  as?: 'div' | 'section' | 'article' | 'li'
}

export function Card({ children, className, padding = 'md', flat = false, as = 'div' }: CardProps) {
  const Component = as
  return (
    <Component
      className={cn(
        'rounded-[var(--radius-card)] border border-line bg-surface',
        flat ? '' : 'shadow-soft',
        PADDING[padding],
        className,
      )}
    >
      {children}
    </Component>
  )
}

export interface CardHeaderProps {
  title: ReactNode
  description?: ReactNode
  action?: ReactNode
  icon?: ReactNode
  className?: string
}

export function CardHeader({ title, description, action, icon, className }: CardHeaderProps) {
  return (
    <div className={cn('flex items-start justify-between gap-4', className)}>
      <div className="flex min-w-0 items-start gap-3">
        {icon ? (
          <span className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-brand-700">
            {icon}
          </span>
        ) : null}
        <div className="min-w-0">
          <h2 className="truncate text-[15px] font-semibold text-ink-900">{title}</h2>
          {description ? <p className="mt-0.5 text-[13px] text-ink-500">{description}</p> : null}
        </div>
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  )
}

/** A label/value pair. The value keeps its own line so tables stay scannable. */
export function DetailRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 py-1.5">
      <dt className="text-[12px] font-medium uppercase tracking-wide text-ink-400">{label}</dt>
      <dd className="text-sm text-ink-800">{value}</dd>
    </div>
  )
}
