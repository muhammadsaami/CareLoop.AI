import { cn } from '@/lib/cn'
import type { ReactNode } from 'react'

export type BadgeTone =
  | 'neutral'
  | 'brand'
  | 'success'
  | 'warning'
  | 'danger'
  | 'info'

const TONES: Record<BadgeTone, string> = {
  neutral: 'bg-ink-100 text-ink-700 ring-ink-200',
  brand: 'bg-brand-50 text-brand-800 ring-brand-200',
  success: 'bg-success-50 text-success-700 ring-success-200',
  warning: 'bg-warning-50 text-warning-700 ring-warning-200',
  danger: 'bg-danger-50 text-danger-700 ring-danger-200',
  info: 'bg-info-50 text-info-700 ring-info-200',
}

export interface BadgeProps {
  children: ReactNode
  tone?: BadgeTone
  /** Adds a leading status dot. */
  dot?: boolean
  className?: string
  size?: 'sm' | 'md'
  /** Native tooltip, e.g. the machine-readable safety flag behind friendly copy. */
  title?: string
}

export function Badge({
  children,
  tone = 'neutral',
  dot = false,
  className,
  size = 'md',
  title,
}: BadgeProps) {
  return (
    <span
      title={title}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full font-medium ring-1 ring-inset',
        size === 'sm' ? 'px-2 py-0.5 text-[11px]' : 'px-2.5 py-1 text-xs',
        TONES[tone],
        className,
      )}
    >
      {dot ? <span className="size-1.5 rounded-full bg-current opacity-70" aria-hidden="true" /> : null}
      {children}
    </span>
  )
}
