import { useState } from 'react'
import { Alert } from '@/components/Alert'
import { Badge } from '@/components/Badge'
import { Button } from '@/components/Button'
import { Icon } from '@/components/Icon'
import {
  useAdherence,
  useRecordAdherence,
  useUpdateAdherence,
} from '@/hooks/usePatientData'
import { ApiError } from '@/lib/apiClient'
import { dateIsoInZone, formatTime, todayIso } from '@/lib/format'
import type { AdherenceLog, Medication } from '@/types/api'

interface AdherenceControlProps {
  patientId: string
  /** Patient's IANA timezone, used to decide which stored log counts as "today". */
  patientTimezone?: string | null
  medication: Medication
}

type Choice = 'taken' | 'notTaken'

/**
 * Record today's dose for one medicine against the real adherence API.
 *
 * The backend contract has exactly one state axis for a dose, `taken: bool`
 * (`POST /patients/{id}/adherence`, `PATCH /adherence/{id}`) and no dose slots,
 * so this component records one self-reported event per medicine per day rather
 * than inventing taken/missed/skipped enums the API does not return.
 *
 * Concurrency rules:
 *  - While a write is in flight both buttons are disabled, so a double tap
 *    cannot POST twice (the backend is intentionally not idempotent).
 *  - When today's record already holds the same value, the tap is a no-op.
 *  - A contrary tap corrects the existing record via PATCH, never a second
 *    POST, so one day never gains duplicate rows.
 *  - After success the adherence query is invalidated and the strip re-reads
 *    the server; the UI is never trusted to remember the write locally.
 */
export function AdherenceControl({ patientId, patientTimezone, medication }: AdherenceControlProps) {
  const adherence = useAdherence(patientId)
  const record = useRecordAdherence()
  const update = useUpdateAdherence()
  const [choice, setChoice] = useState<Choice | null>(null)
  const [error, setError] = useState<string | null>(null)

  const busy = record.isPending || update.isPending

  const todayKey = todayIso(patientTimezone)
  const todayLog: AdherenceLog | null =
    (adherence.data ?? [])
      .filter((log) => log.medication_id === medication.id)
      .filter((log) => dateIsoInZone(log.scheduled_time, patientTimezone) === todayKey)
      .sort((a, b) => b.scheduled_time.localeCompare(a.scheduled_time))[0] ?? null
  const takenToday = todayLog?.taken === true

  function submit(next: Choice, nowIso: string) {
    setError(null)
    setChoice(next)
    const taken = next === 'taken'

    // Idempotent: the day's record already holds exactly this value.
    if (todayLog && todayLog.taken === taken) return

    const onError = (cause: unknown) => setError(messageFor(cause))

    if (todayLog) {
      // Correcting what was recorded earlier today: update in place, never a
      // second row.
      update.mutate(
        {
          patientId,
          adherenceId: todayLog.id,
          body: taken ? { taken, taken_time: nowIso } : { taken },
        },
        { onError },
      )
      return
    }

    record.mutate(
      {
        patientId,
        body: {
          medication_id: medication.id,
          scheduled_time: nowIso,
          taken,
          ...(taken ? { taken_time: nowIso } : {}),
        },
      },
      { onError },
    )
  }

  return (
    <div className="w-full rounded-[var(--radius-control)] border border-line bg-surface-muted p-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[13px] font-medium text-ink-700">Today's dose</p>
        {todayLog ? (
          takenToday ? (
            <Badge tone="success" size="sm" dot>
              Taken{todayLog.taken_time ? ` at ${formatTime(todayLog.taken_time)}` : ' today'}
            </Badge>
          ) : (
            <Badge tone="warning" size="sm" dot>
              Not taken
            </Badge>
          )
        ) : (
          <span className="text-[12px] text-ink-400">Not recorded yet</span>
        )}
      </div>

      <div className="mt-2.5 flex gap-2">
        <Button
          variant="primary"
          size="sm"
          leadingIcon={<Icon name="check" size={14} />}
          loading={busy && choice === 'taken'}
          disabled={busy || takenToday}
          onClick={() => submit('taken', new Date().toISOString())}
        >
          Taken
        </Button>
        <Button
          variant="secondary"
          size="sm"
          leadingIcon={<Icon name="close" size={14} />}
          loading={busy && choice === 'notTaken'}
          disabled={busy || (todayLog !== null && !takenToday)}
          onClick={() => submit('notTaken', new Date().toISOString())}
        >
          Not taken
        </Button>
      </div>

      {error ? <Alert tone="danger" className="mt-2.5">{error}</Alert> : null}
    </div>
  )
}

function messageFor(cause: unknown): string {
  return cause instanceof ApiError ? cause.userMessage : 'Something went wrong. Please try again.'
}