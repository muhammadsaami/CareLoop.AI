import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Alert } from '@/components/Alert'
import { Button, ButtonLink } from '@/components/Button'
import { Card, CardHeader } from '@/components/Card'
import { AppointmentRow } from '@/components/AppointmentCard'
import { EmptyState } from '@/components/EmptyState'
import { ErrorState } from '@/components/ErrorState'
import { Icon } from '@/components/Icon'
import { LoadingState } from '@/components/LoadingState'
import { MedicationRow } from '@/components/MedicationCard'
import { ReviewBanner } from '@/components/ReviewBanner'
import { PageHeader } from '@/layouts/AppShell'
import { StatTile } from '@/features/dashboard/StatTile'
import { useActivePatient } from '@/features/auth/useActivePatient'
import {
  useAppointments,
  useCheckinHistory,
  useDocuments,
  useEscalations,
  useLatestCheckin,
  useMedications,
  useNotifications,
} from '@/hooks/usePatientData'
import { formatDate, todayIso } from '@/lib/format'
import type { DailyCheckIn } from '@/types/api'

/**
 * Patient dashboard.
 *
 * Ordered by what a recovering person actually needs, in order of urgency:
 *   1. an unresolved care-team alert, if one exists
 *   2. today's check-in status
 *   3. the counts
 *   4. the next appointments and current medicines
 *
 * The order is deliberate: nothing about a "good" count is worth showing above
 * a warning that something changed and a person needs to read it.
 */
export function DashboardPage() {
  const { patient, patientId, patients } = useActivePatient()
  const navigate = useNavigate()

  const medications = useMedications(patientId)
  const appointments = useAppointments(patientId)
  const documents = useDocuments(patientId)
  const latestCheckin = useLatestCheckin(patientId)
  const history = useCheckinHistory(patientId)
  const escalations = useEscalations(patientId)
  const notifications = useNotifications(patientId, {})

  const documentsLoading = documents.isLoading
  const escalationsLoading = escalations.isLoading

  const firstName = useMemo(() => patient?.name.trim().split(/\s+/)[0] ?? '', [patient])

  // "Now" is read once per mount rather than during render, so the filter below
  // is pure and two renders in the same second cannot disagree.
  const [now] = useState(() => Date.now())
  const today = useMemo(
    () =>
      new Date().toLocaleDateString(undefined, {
        weekday: 'long',
        day: 'numeric',
        month: 'long',
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- computed once, like `now`
    [],
  )

  const upcoming = useMemo(
    () =>
      (appointments.data ?? [])
        .filter((appointment) => new Date(appointment.date).getTime() >= now - 86_400_000)
        .sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime())
        .slice(0, 4),
    [appointments.data, now],
  )

  const documentsNeedingReview = useMemo(
    () => (documents.data ?? []).filter((doc) => doc.extraction_status === 'needs_review').length,
    [documents.data],
  )

  const openEscalations = useMemo(
    () =>
      (escalations.data ?? []).filter((escalation) =>
        ['pending', 'notified', 'acknowledged'].includes(escalation.status),
      ),
    [escalations.data],
  )

  const failedNotifications = useMemo(
    () => (notifications.data?.notifications ?? []).filter((item) => item.status === 'failed').length,
    [notifications.data],
  )

  const checkinState = useCheckinState(latestCheckin.data, history.data)

  if (medications.isError || appointments.isError) {
    return (
      <div className="px-4 py-6 sm:px-6 lg:px-8">
        <PageHeader title="Dashboard" />
        <ErrorState
          error={medications.error ?? appointments.error}
          onRetry={() => {
            void medications.refetch()
            void appointments.refetch()
          }}
        />
      </div>
    )
  }

  return (
    <div className="px-4 py-6 sm:px-6 lg:px-8">
      <PageHeader
        eyebrow={
          <p className="text-[13px] text-ink-400" data-numeric>
            {today}
          </p>
        }
        title={firstName ? `Hello, ${firstName}` : 'Your recovery'}
        description="Here is where things stand today."
        actions={
          <>
            <ButtonLink to="/check-in" variant="primary" leadingIcon={<Icon name="clipboard" size={18} />}>
              {checkinState.doneToday ? 'View check-in' : 'Check in'}
            </ButtonLink>
            <ButtonLink to="/assistant" variant="secondary" leadingIcon={<Icon name="sparkle" size={18} />}>
              Ask a question
            </ButtonLink>
          </>
        }
      />

      {patients.length > 1 ? (
        <p className="mb-4 text-[13px] text-ink-500">
          You are viewing one of {patients.length} patients your access covers. Use the selector in
          the sidebar to switch.
        </p>
      ) : null}

      {/* 1. Unresolved escalations outrank everything else on this page. */}
      {!escalationsLoading && openEscalations.length > 0 ? (
        <div className="mb-5 space-y-3">
          {openEscalations.slice(0, 2).map((escalation) => (
            <ReviewBanner
              key={escalation.id}
              tone="danger"
              title={
                escalation.status === 'notified'
                  ? 'Your care team has been alerted'
                  : 'A change needs attention'
              }
              reason={`${escalation.reason_code.replace(/_/g, ' ')}. This was raised by your daily check-in on ${formatDate(
                escalation.created_at,
              )}.`}
              action={
                <Button variant="secondary" size="sm" onClick={() => navigate('/caregiver')}>
                  View
                </Button>
              }
            />
          ))}
        </div>
      ) : null}

      {/* 2. Today's check-in. */}
      {checkinState.needsReview ? (
        <ReviewBanner
          className="mb-5"
          reason={checkinState.reviewReason}
          action={
            <Button variant="secondary" size="sm" onClick={() => navigate('/check-in')}>
              Open check-in
            </Button>
          }
        />
      ) : null}

      {checkinState.doneToday ? (
        <Alert tone="success" className="mb-5">
          Today's check-in is done. Answers are recorded and any change that needs attention was
          sent to your care team.
        </Alert>
      ) : (
        <Card className="mb-5" padding="md">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-3">
              <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
                <Icon name="clipboard" size={18} />
              </span>
              <div>
                <p className="text-[15px] font-semibold text-ink-900">Your daily check-in is open</p>
                <p className="mt-0.5 text-[13px] text-ink-500">
                  Three quick questions about how you are today. It takes under a minute.
                </p>
              </div>
            </div>
            <Button className="shrink-0" onClick={() => navigate('/check-in')}>
              Start check-in
            </Button>
          </div>
        </Card>
      )}

      {/* 3. Counts. */}
      <div className="mb-6 grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
        {medications.isLoading || documentsLoading ? (
          Array.from({ length: 4 }, (_, index) => (
            <div key={index} className="h-24 animate-pulse rounded-[var(--radius-card)] bg-ink-100" />
          ))
        ) : (
          <>
            <StatTile
              label="Medicines"
              value={medications.data?.length ?? 0}
              icon="pill"
              tone="brand"
              onClick={() => navigate('/medications')}
            />
            <StatTile
              label="Upcoming visits"
              value={upcoming.length}
              icon="calendar"
              tone="info"
              onClick={() => navigate('/appointments')}
            />
            <StatTile
              label="Needs review"
              value={documentsNeedingReview}
              icon="file"
              tone={documentsNeedingReview > 0 ? 'warning' : 'neutral'}
              detail={documentsNeedingReview > 0 ? 'from your documents' : 'all documents confirmed'}
              onClick={() => navigate('/documents')}
            />
            <StatTile
              label="Failed messages"
              value={failedNotifications}
              icon="bell"
              tone={failedNotifications > 0 ? 'danger' : 'neutral'}
              detail={failedNotifications > 0 ? 'not delivered' : 'all delivered'}
              onClick={() => navigate('/notifications')}
            />
          </>
        )}
      </div>

      {/* 4. Detail lists. */}
      <div className="grid gap-5 lg:grid-cols-2">
        <Card padding="md">
          <CardHeader
            title="Next appointments"
            icon={<Icon name="calendar" size={18} />}
            action={
              <Button variant="ghost" size="sm" onClick={() => navigate('/appointments')}>
                All
              </Button>
            }
          />
          <div className="mt-2">
            {appointments.isLoading ? (
              <LoadingState label="Loading appointments" />
            ) : upcoming.length === 0 ? (
              <EmptyState
                size="sm"
                title="No upcoming appointments"
                description="Add one if your care team has booked a follow-up."
                action={
                  <Button variant="subtle" size="sm" onClick={() => navigate('/appointments')}>
                    Add appointment
                  </Button>
                }
              />
            ) : (
              <ul className="divide-y divide-line">
                {upcoming.map((appointment) => (
                  <AppointmentRow key={appointment.id} appointment={appointment} />
                ))}
              </ul>
            )}
          </div>
        </Card>

        <Card padding="md">
          <CardHeader
            title="Your medicines"
            icon={<Icon name="pill" size={18} />}
            action={
              <Button variant="ghost" size="sm" onClick={() => navigate('/medications')}>
                All
              </Button>
            }
          />
          <div className="mt-2">
            {medications.isLoading ? (
              <LoadingState label="Loading medicines" />
            ) : (medications.data?.length ?? 0) === 0 ? (
              <EmptyState
                size="sm"
                title="No medicines yet"
                description="Upload your discharge paperwork and CareLoop will extract them for you to confirm."
                action={
                  <Button variant="subtle" size="sm" onClick={() => navigate('/documents')}>
                    Upload a document
                  </Button>
                }
              />
            ) : (
              <ul className="divide-y divide-line">
                {(medications.data ?? []).slice(0, 5).map((medication) => (
                  <MedicationRow key={medication.id} medication={medication} />
                ))}
              </ul>
            )}
          </div>
        </Card>
      </div>

      {patient?.discharge_date ? (
        <p className="mt-6 text-[13px] text-ink-400">
          Discharged {formatDate(patient.discharge_date)}
          {history.data?.total ? ` · ${history.data.total} check-ins recorded` : ''}
        </p>
      ) : null}
    </div>
  )
}

interface CheckinState {
  doneToday: boolean
  needsReview: boolean
  reviewReason: string | null
}

function useCheckinState(
  latest: DailyCheckIn | null | undefined,
  history: { items: Array<{ date: string }> } | undefined,
): CheckinState {
  return useMemo(() => {
    const today = todayIso()
    // `latest` is null until the patient has ever checked in; the history
    // envelope is the fallback so a single completed check-in still counts.
    const doneToday =
      (latest && isSameDay(latest.date, today)) ||
      (history?.items ?? []).some((item) => isSameDay(item.date, today))

    return {
      doneToday,
      needsReview: Boolean(latest?.needs_review),
      reviewReason: latest?.review_reason ?? null,
    }
  }, [latest, history])
}

/** Compares calendar days only, so a timezone offset cannot flip the answer. */
function isSameDay(left: string, right: string): boolean {
  return left.slice(0, 10) === right.slice(0, 10)
}
