import { useState } from 'react'
import { Alert } from '@/components/Alert'
import { Badge } from '@/components/Badge'
import { Button } from '@/components/Button'
import { Card, CardHeader } from '@/components/Card'
import { EmptyState } from '@/components/EmptyState'
import { ErrorState, errorMessage } from '@/components/ErrorState'
import { Icon } from '@/components/Icon'
import { LoadingState } from '@/components/LoadingState'
import { Modal } from '@/components/Modal'
import { ReviewBanner } from '@/components/ReviewBanner'
import { Textarea } from '@/components/Input'
import { PageHeader } from '@/layouts/AppShell'
import { useActivePatient } from '@/features/auth/useActivePatient'
import { useEscalationAction, useEscalations, useNotifications, useReminders, useUpdateReminder } from '@/hooks/usePatientData'
import { formatDate, formatDateTime, humanizeEnum } from '@/lib/format'
import { ENUM_LABELS, type Escalation, type EscalationStatus, type Reminder } from '@/types/api'

/**
 * Care team.
 *
 * The backend has NO caregiver-management API. A patient's caregiver is a
 * single `caregiver_contact` string on the patient record, and the only
 * caregiver-related behaviour that exists is Phase 6's escalation, which
 * notifies that number when a daily check-in changes. So this screen shows what
 * is true — who gets alerted, and what has been raised — and the "add a
 * caregiver" affordance is deliberately absent rather than faked.
 */
export function CaregiverPage() {
  const { patient, patientId } = useActivePatient()
  const escalations = useEscalations(patientId)
  const notifications = useNotifications(patientId, {})
  const reminders = useReminders(patientId)
  const [resolving, setResolving] = useState<Escalation | null>(null)

  const caregiverContact = patient?.caregiver_contact ?? null

  // Alerts actually addressed to the caregiver, which is the honest measure of
  // "what has the care team been told".
  const caregiverAlerts = (notifications.data?.notifications ?? []).filter(
    (item) => item.notification_type === 'escalation_notice',
  )
  const blocked = (escalations.data ?? []).filter((item) => item.notification_blocked_reason)

  return (
    <div className="px-4 py-6 sm:px-6 lg:px-8">
      <PageHeader
        title="Care team"
        description="Who is notified when something changes, and what has been raised for them."
      />

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
        <div className="space-y-5">
          <Card padding="md">
            <CardHeader title="Caregiver contact" icon={<Icon name="users" size={18} />} />
            {caregiverContact ? (
              <div className="mt-3">
                <p className="flex items-center gap-2 font-mono text-[15px] text-ink-900">
                  <Icon name="phone" size={16} className="text-ink-400" />
                  {caregiverContact}
                </p>
                <p className="mt-2 text-[13px] leading-relaxed text-ink-500">
                  Alerts from your daily check-in are sent to this number. Your own number is never
                  used for a caregiver alert.
                </p>
              </div>
            ) : (
              <div className="mt-3">
                <Alert tone="warning" title="No caregiver contact is recorded">
                  Without a caregiver contact, CareLoop cannot notify anyone when a daily check-in
                  changes. An alert is still recorded, but it is marked as not delivered. Ask your
                  care team to add a number.
                </Alert>
              </div>
            )}
          </Card>

          <Card padding="md">
            <CardHeader
              title="Alerts sent to the care team"
              description={`${caregiverAlerts.length} sent`}
              icon={<Icon name="bell" size={18} />}
            />
            <div className="mt-3">
              {notifications.isLoading ? (
                <LoadingState label="Loading alerts" />
              ) : caregiverAlerts.length === 0 ? (
                <EmptyState size="sm" title="No alerts sent" description="Nothing has needed alerting yet." />
              ) : (
                <ul className="space-y-2">
                  {caregiverAlerts.slice(0, 5).map((alert) => (
                    <li
                      key={alert.id}
                      className="rounded-[var(--radius-control)] border border-line bg-surface-muted px-3 py-2.5"
                    >
                      <div className="flex items-start justify-between gap-2">
                        <p className="text-[13px] text-ink-800">{alert.body}</p>
                        <Badge tone={alert.status === 'sent' ? 'success' : 'warning'} size="sm" dot>
                          {humanizeEnum(alert.status, ENUM_LABELS.notificationStatus)}
                        </Badge>
                      </div>
                      <p className="mt-1 text-[12px] text-ink-400" data-numeric>
                        {formatDateTime(alert.scheduled_for)} · to {alert.recipient}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </Card>

          <Card padding="md">
            {/* Not "on this device": reminders are records on the CareLoop
                service, delivered to the contact number on file. Saying
                otherwise would imply a browser-side feature that does not
                exist and would understate what a caregiver receives. */}
            <CardHeader
              title="Your reminders"
              description="Sent to the contact number on file for this patient"
              icon={<Icon name="bell" size={18} />}
            />
            <div className="mt-3">
              {reminders.isLoading ? (
                <LoadingState label="Loading reminders" />
              ) : (reminders.data?.length ?? 0) === 0 ? (
                <EmptyState size="sm" title="No reminders set" description="Reminders you create appear here." />
              ) : (
                <ul className="space-y-2">
                  {(reminders.data ?? []).map((reminder) => (
                    <ReminderRow key={reminder.id} reminder={reminder} patientId={patientId!} />
                  ))}
                </ul>
              )}
            </div>
          </Card>
        </div>

        <Card padding="md" className="h-fit">
          <CardHeader
            title="Raised for review"
            description="Changes from daily check-ins that need a person to look at them"
            icon={<Icon name="alert" size={18} />}
          />

          <div className="mt-3 space-y-3">
            {escalations.isError ? (
              <ErrorState error={escalations.error} onRetry={() => void escalations.refetch()} compact />
            ) : escalations.isLoading ? (
              <LoadingState label="Loading escalations" />
            ) : (escalations.data?.length ?? 0) === 0 ? (
              <EmptyState
                icon={<Icon name="check" size={24} />}
                title="Nothing has been raised"
                description="If a daily check-in shows one of your warning symptoms has got worse, it will appear here for your care team to review."
              />
            ) : (
              <>
                {blocked.length > 0 ? (
                  <ReviewBanner
                    tone="danger"
                    title="An alert could not be delivered"
                    reason={blocked
                      .map((item) => `${item.reason_code.replace(/_/g, ' ')}: ${item.notification_blocked_reason}`)
                      .join(' · ')}
                  />
                ) : null}

                <ul className="space-y-3">
                  {(escalations.data ?? []).map((escalation) => (
                    <EscalationRow
                      key={escalation.id}
                      escalation={escalation}
                      patientId={patientId!}
                      onResolve={() => setResolving(escalation)}
                    />
                  ))}
                </ul>
              </>
            )}
          </div>
        </Card>
      </div>

      {resolving ? (
        <ResolveDialog escalation={resolving} patientId={patientId!} onClose={() => setResolving(null)} />
      ) : null}
    </div>
  )
}

const ESCALATION_TONE: Record<EscalationStatus, 'neutral' | 'warning' | 'danger' | 'success' | 'info'> = {
  pending: 'warning',
  notified: 'danger',
  acknowledged: 'info',
  resolved: 'success',
  cancelled: 'neutral',
}

function EscalationRow({
  escalation,
  patientId,
  onResolve,
}: {
  escalation: Escalation
  patientId: string
  onResolve: () => void
}) {
  const action = useEscalationAction(patientId)
  const [error, setError] = useState<string | null>(null)

  const settled = escalation.status === 'resolved' || escalation.status === 'cancelled'
  const canAcknowledge = escalation.status === 'pending' || escalation.status === 'notified'

  function run(kind: 'acknowledge' | 'cancel') {
    setError(null)
    action.mutate(
      { escalationId: escalation.id, action: kind },
      { onError: (cause) => setError(errorMessage(cause)) },
    )
  }

  return (
    <li className="rounded-[var(--radius-card)] border border-line bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-ink-900">
            {ENUM_LABELS.escalationCategory[escalation.category] ?? escalation.category}
          </p>
          <p className="mt-0.5 text-[12px] text-ink-500" data-numeric>
            Raised {formatDateTime(escalation.created_at)} · rule {escalation.rule_code}
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          {escalation.severity ? (
            <Badge tone="danger" size="sm">
              {escalation.severity}
            </Badge>
          ) : null}
          <Badge tone={ESCALATION_TONE[escalation.status] ?? 'neutral'} size="sm" dot>
            {humanizeEnum(escalation.status, ENUM_LABELS.escalationStatus)}
          </Badge>
        </div>
      </div>

      <p className="mt-2 text-[13px] leading-relaxed text-ink-600">
        Workflow: {humanizeEnum(escalation.workflow, ENUM_LABELS.escalationWorkflow)}
      </p>

      {escalation.notification_blocked_reason ? (
        <p className="mt-2 rounded-[var(--radius-control)] border border-danger-200 bg-danger-50 px-3 py-2 text-[13px] text-danger-700">
          Not delivered: {escalation.notification_blocked_reason}
        </p>
      ) : null}

      {escalation.resolution_note ? (
        <p className="mt-2 rounded-[var(--radius-control)] bg-ink-50 px-3 py-2 text-[13px] text-ink-600">
          {escalation.resolution_note}
        </p>
      ) : null}

      {error ? (
        <p role="alert" className="mt-2 text-[13px] font-medium text-danger-700">
          {error}
        </p>
      ) : null}

      {!settled ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {canAcknowledge ? (
            <Button
              size="sm"
              variant="secondary"
              loading={action.isPending && action.variables?.action === 'acknowledge'}
              onClick={() => run('acknowledge')}
            >
              Acknowledge
            </Button>
          ) : null}
          <Button size="sm" variant="subtle" onClick={onResolve}>
            Mark resolved
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="text-danger-600 hover:bg-danger-50"
            loading={action.isPending && action.variables?.action === 'cancel'}
            onClick={() => run('cancel')}
          >
            Cancel
          </Button>
        </div>
      ) : null}
    </li>
  )
}

function ResolveDialog({
  escalation,
  patientId,
  onClose,
}: {
  escalation: Escalation
  patientId: string
  onClose: () => void
}) {
  const action = useEscalationAction(patientId)
  const [note, setNote] = useState('')

  return (
    <Modal
      open
      onClose={onClose}
      title="Mark this as resolved"
      description="Add a short note about what was decided. This is kept with the escalation."
      size="sm"
      busy={action.isPending}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={action.isPending}>
            Cancel
          </Button>
          <Button
            loading={action.isPending}
            onClick={() =>
              action.mutate(
                { escalationId: escalation.id, action: 'resolve', note: note.trim() || undefined },
                { onSuccess: onClose },
              )
            }
          >
            Mark resolved
          </Button>
        </>
      }
    >
      <Textarea
        label="Resolution note"
        placeholder="e.g. Called the patient, symptoms settled, no further action needed."
        value={note}
        onChange={(event) => setNote(event.target.value)}
        rows={3}
        maxLength={2000}
      />
      {action.error ? (
        <Alert tone="danger" className="mt-3">
          {errorMessage(action.error)}
        </Alert>
      ) : null}
    </Modal>
  )
}

function ReminderRow({ reminder, patientId }: { reminder: Reminder; patientId: string }) {
  const update = useUpdateReminder()
  const active = reminder.status === 'active'

  return (
    <li className="rounded-[var(--radius-control)] border border-line bg-surface-muted px-3 py-2.5">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-[13px] font-medium text-ink-800">
            {humanizeEnum(reminder.reminder_type, ENUM_LABELS.reminderType)}
            {reminder.frequency_text ? ` · ${reminder.frequency_text}` : ''}
          </p>
          <p className="mt-0.5 text-[12px] text-ink-500">
            {reminder.next_occurrence_at
              ? `Next ${formatDate(reminder.next_occurrence_at)}`
              : 'No next occurrence scheduled'}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {reminder.needs_review ? (
            <Badge tone="warning" size="sm">
              Review
            </Badge>
          ) : null}
          <Badge tone={active ? 'success' : 'neutral'} size="sm" dot>
            {humanizeEnum(reminder.status, ENUM_LABELS.reminderStatus)}
          </Badge>
        </div>
      </div>
      {reminder.review_reason ? (
        <p className="mt-1.5 text-[12px] text-warning-700">{reminder.review_reason}</p>
      ) : null}
      <div className="mt-2">
        <Button
          size="sm"
          variant="ghost"
          loading={update.isPending}
          onClick={() =>
            update.mutate({
              patientId,
              reminderId: reminder.id,
              body: { status: active ? 'paused' : 'active' },
            })
          }
        >
          {active ? 'Pause' : 'Resume'}
        </Button>
      </div>
    </li>
  )
}
