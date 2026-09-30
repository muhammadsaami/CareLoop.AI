import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { Icon, type IconName } from '@/components/Icon'
import { IconContainer, type IconContainerTone } from '@/components/IconContainer'

export interface MetricCardProps {
  /** The card's small title, e.g. "Medications". */
  label: string
  /** The dominant metric, e.g. a count, a short date, or "Day 4". */
  value: ReactNode
  /** The one-line supporting context, e.g. "3 active on your list". */
  detail: ReactNode
  icon: IconName
  tone?: IconContainerTone
  onClick?: () => void
  className?: string
}

/** The Figma-style summary card: icon tile, big metric, label, context, arrow. */
export function MetricCard({
  label,
  value,
  detail,
  icon,
  tone = 'brand',
  onClick,
  className,
}: MetricCardProps) {
  const shell = cn(
    'group flex h-full w-full flex-col rounded-2xl border border-line bg-surface p-5 text-left shadow-soft transition-shadow hover:shadow-raised',
    onClick && 'cursor-pointer outline-none',
    className,
  )

  const body = (
    <>
      <div className="flex items-center justify-between">
        <IconContainer icon={icon} tone={tone} size={18} />
        {onClick ? (
          <Icon
            name="chevronRight"
            size={18}
            className="text-ink-300 transition-colors group-hover:text-ink-500"
          />
        ) : null}
      </div>
      <span className="mt-4 block truncate text-3xl font-semibold tabular-nums tracking-tight text-ink-950">
        {value}
      </span>
      <span className="mt-1 block text-sm font-medium text-ink-700">{label}</span>
      <span className="mt-0.5 block text-[13px] leading-snug text-ink-500">{detail}</span>
    </>
  )

  if (onClick) {
    return (
      <button type="button" onClick={onClick} className={shell}>
        {body}
      </button>
    )
  }

  return <div className={shell}>{body}</div>
}