import { Alert } from '@/components/Alert'
import { Button } from '@/components/Button'
import { Card, CardHeader } from '@/components/Card'
import { Icon } from '@/components/Icon'
import { LoadingState } from '@/components/LoadingState'
import { PageHeader } from '@/layouts/AppShell'
import { CheckInForm, CheckInHistory } from '@/features/checkins/CheckInForm'
import { useScheduleCheckin } from '@/hooks/usePatientData'
import { useActivePatient } from '@/features/auth/useActivePatient'
import { useState } from 'react'
import { Input } from '@/components/Input'
import { Modal } from '@/components/Modal'
import { isValidLocalTime } from '@/lib/format'
import { useReminders } from '@/hooks/usePatientData'

export function CheckInPage() {
  const { patient, patientId } = useActivePatient()
  const [showHistory, setShowHistory] = useState(false)
  const [showSchedule, setShowSchedule] = useState(false)

  if (!patientId || !patient) {
    return (
      <div className="px-4 py-6 sm:px-6 lg:px-8">
        <LoadingState label="Loading patient" />
      </div>
    )
  }

  return (
    <div className="px-4 py-6 sm:px-6 lg:px-8">
      <PageHeader
        eyebrow={
          <span className="inline-flex items-center gap-1.5 text-[13px] text-ink-400">
            <Icon name="shield" size={14} />
            Answers come from a fixed set of choices
          </span>
        }
        title="Daily check-in"
        description="A few questions about how you are today. Your answers are compared with the warning symptoms on your discharge instructions, and your care team is told if something changed."
        actions={
          <>
            <Button variant="secondary" onClick={() => setShowSchedule(true)}>
              Set a reminder
            </Button>
            <Button variant="ghost" onClick={() => setShowHistory((value) => !value)}>
              {showHistory ? 'Hide history' : 'Past check-ins'}
            </Button>
          </>
        }
      />

      <div className="max-w-3xl space-y-5">
        {showHistory ? (
          <Card padding="md">
            <CardHeader title="Past check-ins" description="Most recent first" />
            <div className="mt-3">
              <CheckInHistory patientId={patientId} />
            </div>
          </Card>
        ) : null}

        <CheckInForm patientId={patientId} timezone={patient.timezone} />
      </div>

      {showSchedule ? (
        <ScheduleCheckinModal
          patientId={patientId}
          timezone={patient.timezone}
          onClose={() => setShowSchedule(false)}
        />
      ) : null}
    </div>
  )
}

function ScheduleCheckinModal({
  patientId,
  timezone,
  onClose,
}: {
  patientId: string
  timezone: string
  onClose: () => void
}) {
  const [localTime, setLocalTime] = useState('09:00')
  const [error, setError] = useState<string | null>(null)
  const schedule = useScheduleCheckin()
  const reminders = useReminders(patientId)

  const existing = (reminders.data ?? []).filter(
    (reminder) => reminder.reminder_type === 'checkin',
  )

  async function onSubmit() {
    setError(null)
    if (!isValidLocalTime(localTime)) {
      setError('Use a 24-hour time like 09:00.')
      return
    }
    schedule.mutate(
      { patientId, body: { local_time: localTime, timezone } },
      { onSuccess: onClose },
    )
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Daily check-in reminder"
      description="CareLoop will send you a reminder at this time each day."
      size="sm"
      busy={schedule.isPending}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={schedule.isPending}>
            Cancel
          </Button>
          <Button onClick={() => void onSubmit()} loading={schedule.isPending}>
            Save reminder
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Input
          type="time"
          label="Reminder time"
          value={localTime}
          onChange={(event) => setLocalTime(event.target.value)}
          error={error ?? undefined}
          hint={`Times are in your timezone, ${timezone}.`}
          required
        />

        {existing.length > 0 ? (
          <Alert tone="brand" title="You already have a check-in reminder">
            {existing
              .map((reminder) => `${reminder.local_time ?? '—'} (${reminder.status})`)
              .join(', ')}
            . Saving a new one may create a second reminder.
          </Alert>
        ) : null}
      </div>
    </Modal>
  )
}
