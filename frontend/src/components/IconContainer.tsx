import { cn } from '@/lib/cn'
import { Icon, type IconName } from './Icon'

export type IconContainerTone =
  | 'neutral'
  | 'brand'
  | 'success'
  | 'warning'
  | 'danger'
  | 'info'
  /** For icon tiles that sit on the dark teal check-in card. */
  | 'surface'

const TONES: Record<IconContainerTone, string> = {
  neutral: 'bg-ink-100 text-ink-600',
  brand: 'bg-brand-50 text-brand-600',
  success: 'bg-success-50 text-success-600',
  warning: 'bg-warning-50 text-warning-600',
  danger: 'bg-danger-50 text-danger-600',
  info: 'bg-info-50 text-info-600',
  surface: 'bg-white/15 text-white',
}

export interface IconContainerProps {
  icon: IconName
  tone?: IconContainerTone
  className?: string
  /** Rendered icon size in px. The tile stays a fixed 44px square. */
  size?: number
}

/** The rounded icon tile used by cards to give every action a consistent glyph. */
export function IconContainer({ icon, tone = 'brand', className, size = 20 }: IconContainerProps) {
  return (
    <span
      className={cn(
        'flex size-11 shrink-0 items-center justify-center rounded-xl',
        TONES[tone],
        className,
      )}
      aria-hidden="true"
    >
      <Icon name={icon} size={size} />
    </span>
  )
}