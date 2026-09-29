import { cn } from '@/lib/cn'
import { Icon, type IconName } from '@/components/Icon'
import type { ReactNode } from 'react'

export type StatTone = 'neutral' | 'brand' | 'success' | 'warning' | 'danger' | 'info'

const TONE_STYLES: Record<StatTone, { icon: string; value: string }> = {
  neutral: { icon: 'bg-ink-100 text-ink-500', value: 'text-ink-900' },
  brand: { icon: 'bg-brand-50 text-brand-600', value: 'text-brand-800' },
  success: { icon: 'bg-success-50 text-success-600', value: 'text-success-700' },
  warning: { icon: 'bg-warning-50 text-warning-600', value: 'text-warning-700' },
  danger: { icon: 'bg-danger-50 text-danger-600', value: 'text-danger-700' },
  info: { icon: 'bg-info-50 text-info-600', value: 'text-info-700' },
}

export interface StatTileProps {
  label: string
  value: ReactNode
  icon: IconName
  tone?: StatTone
  /** Secondary line, e.g. "2 need review". */
  detail?: ReactNode
  onClick?: () => void
  className?: string
}

/** A single number with its label. Used only for counts, never for a score. */
export function StatTile({
  label,
  value,
  icon,
  tone = 'neutral',
  detail,
  onClick,
  className,
}: StatTileProps) {
  const styles = TONE_STYLES[tone]

  const body = (
    <>
      <div className="flex items-start justify-between gap-3">
        <p className="text-[12px] font-medium uppercase tracking-wide text-ink-400">{label}</p>
        <span className={cn('flex size-8 shrink-0 items-center justify-center rounded-lg', styles.icon)}>
          <Icon name={icon} size={16} />
        </span>
      </div>
      <p className={cn('mt-2 text-2xl font-semibold tabular-nums tracking-tight', styles.value)}>
        {value}
      </p>
      {detail ? <div className="mt-0.5 text-[12px] text-ink-500">{detail}</div> : null}
    </>
  )

  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        className={cn(
          'w-full rounded-[var(--radius-card)] border border-line bg-surface p-4 text-left shadow-soft transition-shadow hover:shadow-raised',
          className,
        )}
      >
        {body}
      </button>
    )
  }

  return (
    <div
      className={cn(
        'rounded-[var(--radius-card)] border border-line bg-surface p-4 shadow-soft',
        className,
      )}
    >
      {body}
    </div>
  )
}
