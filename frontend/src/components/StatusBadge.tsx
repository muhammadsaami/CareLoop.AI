import type { ReactNode } from 'react'
import { Badge, type BadgeTone } from './Badge'

export interface StatusBadgeProps {
  tone: BadgeTone
  children: ReactNode
  className?: string
}

/**
 * A status pill for page-and-card headers. Carries the leading status dot so
 * every status reads the same across the dashboard.
 */
export function StatusBadge({ tone, children, className }: StatusBadgeProps) {
  return (
    <Badge tone={tone} dot className={className}>
      {children}
    </Badge>
  )
}