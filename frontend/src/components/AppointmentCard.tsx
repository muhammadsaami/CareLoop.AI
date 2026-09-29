import { Badge, type BadgeTone } from './Badge'
import { cn } from '@/lib/cn'
import { formatDateTime } from '@/lib/format'
import { ENUM_LABELS, type Appointment, type AppointmentStatus } from '@/types/api'
import type { ReactNode } from 'react'

const STATUS_TONE: Record<AppointmentStatus, BadgeTone> = {
  scheduled: 'info',
  completed: 'success',
  cancelled: 'neutral',
  missed: 'warning',
}

const CARD_BASE =
  'w-full rounded-[var(--radius-card)] border border-line bg-surface p-4 text-left shadow-soft'

export interface AppointmentCardProps {
  appointment: Appointment
  actions?: ReactNode
  onClick?: () => void
  className?: string
}

export function AppointmentCard({ appointment, actions, onClick, className }: AppointmentCardProps) {
  const statusTone = STATUS_TONE[appointment.status] ?? 'neutral'

  const body = (
    <>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-[15px] font-semibold text-ink-900">{appointment.doctor_name}</p>
          <p className="mt-0.5 text-sm text-ink-600">{formatDateTime(appointment.date)}</p>
        </div>
        <Badge tone={statusTone} dot>
          {ENUM_LABELS.appointmentStatus[appointment.status] ?? appointment.status}
        </Badge>
      </div>

      {appointment.location ? (
        <p className="mt-2.5 flex items-start gap-1.5 text-[13px] text-ink-600">
          <svg
            className="mt-0.5 size-3.5 shrink-0 text-ink-400"
            viewBox="0 0 20 20"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            aria-hidden="true"
          >
            <path d="M10 17s5-4.6 5-8a5 5 0 1 0-10 0c0 3.4 5 8 5 8Z" />
            <circle cx="10" cy="9" r="1.8" />
          </svg>
          <span>{appointment.location}</span>
        </p>
      ) : null}

      {appointment.notes ? (
        <p className="mt-2.5 rounded-[var(--radius-control)] bg-ink-50 px-3 py-2 text-[13px] leading-relaxed text-ink-600">
          {appointment.notes}
        </p>
      ) : null}
    </>
  )

  return (
    <div className={cn('space-y-3', className)}>
      {onClick ? (
        <button
          type="button"
          onClick={onClick}
          className={cn(CARD_BASE, 'transition-shadow duration-150 hover:shadow-raised')}
        >
          {body}
        </button>
      ) : (
        <div className={CARD_BASE}>{body}</div>
      )}
      {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
    </div>
  )
}

/** Compact row for dashboard lists. */
export function AppointmentRow({ appointment }: { appointment: Appointment }) {
  return (
    <li className="flex items-center justify-between gap-3 py-2.5">
      <div className="min-w-0">
        <p className="truncate text-sm font-medium text-ink-800">{appointment.doctor_name}</p>
        <p className="text-[13px] text-ink-500">{formatDateTime(appointment.date)}</p>
      </div>
      <Badge tone={STATUS_TONE[appointment.status] ?? 'neutral'} size="sm" dot>
        {ENUM_LABELS.appointmentStatus[appointment.status] ?? appointment.status}
      </Badge>
    </li>
  )
}
