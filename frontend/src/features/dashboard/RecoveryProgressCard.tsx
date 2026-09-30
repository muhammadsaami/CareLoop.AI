import { useState } from 'react'
import { Card, CardHeader } from '@/components/Card'
import { IconContainer } from '@/components/IconContainer'
import { formatDate } from '@/lib/format'
import { cn } from '@/lib/cn'
import { recoveryDay } from './recoveryDay'

export interface RecoveryProgressCardProps {
  dischargeDate: string | null
  checkInsTotal: number
  isLoading: boolean
  className?: string
}

export function RecoveryProgressCard({
  dischargeDate,
  checkInsTotal,
  isLoading,
  className,
}: RecoveryProgressCardProps) {
  // "Now" is pinned once per mount so the derived day cannot shift mid-render.
  const [now] = useState(() => Date.now())
  const day = recoveryDay(dischargeDate, now)

  if (isLoading) {
    return <div className={cn('h-64 animate-pulse rounded-2xl bg-ink-100', className)} />
  }

  return (
    <Card padding="lg" as="section" className={cn('flex h-full flex-col', className)}>
      <CardHeader
        title="Recovery"
        description="Progress along your discharge plan"
        icon={<IconContainer icon="activity" tone="brand" />}
      />

      <div className="mt-6 flex h-full flex-col">
        {day !== null ? (
          <>
            <p className="text-sm font-medium text-ink-500">Recovery day</p>
            <p className="mt-1 text-4xl font-semibold tabular-nums tracking-tight text-ink-950">
              Day {day}
            </p>
            <p className="mt-1 text-sm text-ink-500">
              Since discharge on {formatDate(dischargeDate)}
              {checkInsTotal > 0
                ? ` · ${checkInsTotal} check-in${checkInsTotal === 1 ? '' : 's'} recorded`
                : null}
            </p>
          </>
        ) : (
          <>
            <p className="text-4xl font-semibold tabular-nums tracking-tight text-ink-950">—</p>
            <p className="mt-1 text-sm text-ink-500">Your discharge date has not been recorded yet.</p>
          </>
        )}

        {/* The track renders so the card keeps its structure; without a planned
            duration there is nothing honestly fillable, so it stays empty. */}
        <div aria-hidden="true" className="mt-6 h-2.5 overflow-hidden rounded-full bg-ink-100">
          <div className="h-full w-0 rounded-full bg-brand-600" />
        </div>
        <p className="mt-3 text-[13px] leading-relaxed text-ink-500">
          Recovery progress will appear here once your recovery plan is available.
        </p>
      </div>
    </Card>
  )
}