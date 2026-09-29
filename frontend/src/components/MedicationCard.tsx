import { Badge } from './Badge'
import { cn } from '@/lib/cn'
import { formatDate } from '@/lib/format'
import type { Medication } from '@/types/api'
import type { ReactNode } from 'react'

export interface MedicationCardProps {
  medication: Medication
  /** Rendered in the header, e.g. an adherence pill. */
  badge?: ReactNode
  actions?: ReactNode
  onClick?: () => void
  className?: string
}

const CARD_BASE =
  'w-full rounded-[var(--radius-card)] border border-line bg-surface p-4 text-left shadow-soft'

/**
 * Name and dosage are the two things a patient must read correctly, so they are
 * the largest type on the card. Instructions are shown verbatim because they
 * came from a clinician's document.
 */
export function MedicationCard({ medication, badge, actions, onClick, className }: MedicationCardProps) {
  const body = (
    <>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-[15px] font-semibold text-ink-900">{medication.name}</p>
          <p className="mt-0.5 text-sm text-ink-600">
            <span className="font-medium text-ink-800">{medication.dosage}</span>
            {medication.frequency ? <span> · {medication.frequency}</span> : null}
          </p>
        </div>
        {badge}
      </div>

      {(medication.timing || medication.start_date || medication.end_date) && (
        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-[13px] sm:grid-cols-3">
          {medication.timing ? (
            <div>
              <dt className="text-ink-400">When</dt>
              <dd className="text-ink-700">{medication.timing}</dd>
            </div>
          ) : null}
          {medication.start_date ? (
            <div>
              <dt className="text-ink-400">Starts</dt>
              <dd className="text-ink-700">{formatDate(medication.start_date)}</dd>
            </div>
          ) : null}
          {medication.end_date ? (
            <div>
              <dt className="text-ink-400">Ends</dt>
              <dd className="text-ink-700">{formatDate(medication.end_date)}</dd>
            </div>
          ) : null}
        </dl>
      )}

      {medication.instructions ? (
        <p className="mt-3 rounded-[var(--radius-control)] bg-ink-50 px-3 py-2 text-[13px] leading-relaxed text-ink-600">
          {medication.instructions}
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

/** Compact row used inside dashboard summaries. */
export function MedicationRow({
  medication,
  status,
  statusTone = 'neutral',
}: {
  medication: Medication
  status?: string
  statusTone?: 'neutral' | 'success' | 'warning' | 'danger'
}) {
  return (
    <li className="flex items-center justify-between gap-3 py-2.5">
      <div className="min-w-0">
        <p className="truncate text-sm font-medium text-ink-800">{medication.name}</p>
        <p className="text-[13px] text-ink-500">
          {medication.dosage} · {medication.frequency}
        </p>
      </div>
      {status ? (
        <Badge tone={statusTone} size="sm" dot>
          {status}
        </Badge>
      ) : null}
    </li>
  )
}
