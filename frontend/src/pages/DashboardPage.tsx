import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
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
import { StatusBadge } from '@/components/StatusBadge'
import { IconContainer } from '@/components/IconContainer'
import {
  MetricCard,
  RecoveryProgressCard,
  WarningSymptomsCard,
  recoveryDay,
} from '@/features/dashboard'
import { useActivePatient } from '@/features/auth/useActivePatient'
import {
  useAppointments,
  useCheckinHistory,
  useEscalations,
  useLatestCheckin,
  useMedications,
  useNotifications,
} from '@/hooks/usePatientData'
import { formatDate, todayIso } from '@/lib/format'
import type { DailyCheckIn } from '@/types/api'

/**
 * Patient dashboard — the Figma layout.
 *
 * Every number here comes from a real query; anything without a data source
 * renders a deliberate empty/neutral state rather than a fabricated figure.
 * Order on the page is by what a recovering person actually needs: care-team
 * alerts first, then the check-in prompt, then the summary cards and detail
 * lists.
 */
export function DashboardPage() {
  const { patient, patientId, patients } = useActivePatient()
  const navigate = useNavigate()

  const medications = useMedications(patientId)
  const appointments = useAppointments(patientId)
  const latestCheckin = useLatestCheckin(patientId)
  const history = useCheckinHistory(patientId)
  const escalations = useEscalations(patientId)
  const notifications = useNotifications(patientId, {})

  const escalationsLoading = escalations.isLoading

  const firstName = useMemo(() => patient?.name.trim().split(/\s+/)[0] ?? '', [patient])

  // "Now" is read once per mount rather than during render, so the filters and
  // the derived recovery day are pure and two renders in the same second
  // cannot disagree.
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
  const day = recoveryDay(patient?.discharge_date ?? null, now)

  const greeting = useMemo(() => {
    const hour = new Date(now).getHours()
    if (hour < 12) return 'Good morning'
    if (hour < 17) return 'Good afternoon'
    return 'Good evening'
  }, [now])

  const upcoming = useMemo(
    () =>
      (appointments.data ?? [])
        .filter((appointment) => new Date(appointment.date).getTime() >= now - 86_400_000)
        .sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime())
        .slice(0, 4),
    [appointments.data, now],
  )

  const nextShortDate = useMemo(() => {
    const next = upcoming[0]
    if (!next) return null
    const date = new Date(next.date)
    return Number.isNaN(date.getTime())
      ? null
      : date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  }, [upcoming])

  const openEscalations = useMemo(
    () =>
      (escalations.data ?? []).filter((escalation) =>
        ['pending', 'notified', 'acknowledged'].includes(escalation.status),
      ),
    [escalations.data],
  )

  const notificationCount = notifications.data?.notifications.length ?? 0

  const checkinState = useCheckinState(latestCheckin.data, history.data)

  const medicationCount = medications.data?.length ?? 0
  const [dismissCheckin, setDismissCheckin] = useState(false)

  const statusBadge = checkinState.doneToday
    ? { tone: 'success' as const, label: 'Checked in today' }
    : openEscalations.length > 0
      ? { tone: 'danger' as const, label: 'Needs attention' }
      : day !== null
        ? { tone: 'brand' as const, label: `Recovery day ${day}` }
        : { tone: 'neutral' as const, label: 'Getting started' }

  if (medications.isError || appointments.isError) {
    return (
      <div className="px-4 py-6 sm:px-6 lg:px-8">
        <PageHeaderFallback />
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
      {/* Greeting header + status */}
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="min-w-0">
          <p className="text-[13px] font-medium text-ink-400" data-numeric>
            {day !== null ? `Day ${day} of recovery · ${today}` : today}
          </p>
          <h1 className="mt-1.5 text-3xl font-semibold tracking-tight text-ink-950 sm:text-4xl">
            {firstName ? `${greeting}, ${firstName}.` : 'Welcome back.'}
          </h1>
          <p className="mt-2 text-[15px] text-ink-500">Here&apos;s your summary for today.</p>
        </div>
        <div className="flex shrink-0 flex-col gap-3 sm:flex-row sm:items-center lg:flex-col lg:items-end">
          <StatusBadge tone={statusBadge.tone}>{statusBadge.label}</StatusBadge>
          <div className="flex flex-wrap gap-2">
            <ButtonLink
              to="/check-in"
              variant="secondary"
              size="sm"
              leadingIcon={<Icon name="clipboard" size={16} />}
            >
              {checkinState.doneToday ? 'View check-in' : 'Check in'}
            </ButtonLink>
            <ButtonLink
              to="/assistant"
              variant="secondary"
              size="sm"
              leadingIcon={<Icon name="sparkle" size={16} />}
            >
              Ask a question
            </ButtonLink>
          </div>
        </div>
      </div>

      {patients.length > 1 ? (
        <p className="mt-4 text-[13px] text-ink-500">
          You are viewing one of {patients.length} patients your access covers. Use the selector in
          the sidebar to switch.
        </p>
      ) : null}

      {/* 1. Unresolved escalations outrank everything else on this page. */}
      {!escalationsLoading && openEscalations.length > 0 ? (
        <div className="mt-6 space-y-3">
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
          className="mt-6"
          reason={checkinState.reviewReason}
          action={
            <Button variant="secondary" size="sm" onClick={() => navigate('/check-in')}>
              Open check-in
            </Button>
          }
        />
      ) : checkinState.doneToday ? (
        <Alert tone="success" className="mt-6">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <span>
              Today&apos;s check-in is done. Answers are recorded and any change that needs attention
              was sent to your care team.
            </span>
            <ButtonLink to="/check-in" variant="subtle" size="sm" className="shrink-0">
              View today&apos;s check-in
            </ButtonLink>
          </div>
        </Alert>
      ) : !dismissCheckin ? (
        <CheckInCard onDismiss={() => setDismissCheckin(true)} />
      ) : null}

      {/* 3. The four summary cards. */}
      {medications.isLoading || appointments.isLoading ? (
        <div className="mt-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
          {Array.from({ length: 4 }, (_, index) => (
            <div key={index} className="h-40 animate-pulse rounded-2xl bg-ink-100" />
          ))}
        </div>
      ) : (
        <div className="mt-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
          <MetricCard
            label="Medications"
            value={medicationCount}
            detail={
              medicationCount > 0
                ? `${medicationCount} ${medicationCount === 1 ? 'medicine' : 'medicines'} on your list`
                : 'No medicines recorded'
            }
            icon="pill"
            tone="brand"
            onClick={() => navigate('/medications')}
          />
          <MetricCard
            label="Next appointment"
            value={nextShortDate ?? '—'}
            detail={upcoming[0]?.doctor_name ?? 'No upcoming appointments'}
            icon="calendar"
            tone="info"
            onClick={() => navigate('/appointments')}
          />
          <MetricCard
            label="Notifications"
            value={notificationCount}
            detail={notificationCount > 0 ? 'From CareLoop' : 'No notifications yet'}
            icon="bell"
            tone="neutral"
            onClick={() => navigate('/notifications')}
          />
          <MetricCard
            label="Recovery"
            value={day !== null ? `Day ${day}` : '—'}
            detail={
              patient?.discharge_date
                ? `Since ${formatDate(patient.discharge_date)}`
                : 'Waiting to begin'
            }
            icon="heart"
            tone="success"
            onClick={() => navigate('/check-in')}
          />
        </div>
      )}

      {/* 4. Recovery progress + warning symptoms. */}
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <RecoveryProgressCard
          dischargeDate={patient?.discharge_date ?? null}
          checkInsTotal={history.data?.total ?? 0}
          isLoading={history.isLoading}
        />
        <WarningSymptomsCard patientId={patientId} />
      </div>

      {/* 5. Detail lists. */}
      <div className="mt-4 grid gap-5 lg:grid-cols-2">
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
            ) : medicationCount === 0 ? (
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

/** The full-width teal daily check-in invitation. */
function CheckInCard({ onDismiss }: { onDismiss: () => void }) {
  return (
    <section className="relative mt-6 overflow-hidden rounded-2xl bg-brand-700 p-6 shadow-soft sm:p-7">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -right-20 -top-24 size-64 rounded-full bg-white/10 blur-3xl"
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -bottom-28 -left-10 size-72 rounded-full bg-brand-500/25 blur-3xl"
      />
      <div className="relative flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-4">
          <IconContainer icon="clipboard" tone="surface" />
          <div>
            <h2 className="text-lg font-semibold tracking-tight text-white">
              Your daily check-in is ready
            </h2>
            <p className="mt-1 max-w-md text-sm leading-relaxed text-brand-50/90">
              Three quick questions about how you&apos;re feeling today. It takes under a minute.
            </p>
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-3">
          <Link
            to="/check-in"
            className="inline-flex h-11 items-center gap-2 rounded-xl bg-white px-5 text-[15px] font-semibold text-brand-700 shadow-soft transition-colors hover:bg-brand-50"
          >
            <Icon name="clipboard" size={18} />
            Start check-in
          </Link>
          <button
            type="button"
            onClick={onDismiss}
            className="rounded-xl px-3 py-2 text-sm font-medium text-brand-50/85 transition-colors hover:bg-white/10 hover:text-white"
          >
            Later
          </button>
        </div>
      </div>
    </section>
  )
}

/** The dashboard does not use the generic PageHeader; render its bare title. */
function PageHeaderFallback() {
  return <h1 className="mb-6 text-2xl font-semibold tracking-tight text-ink-950 sm:text-[28px]">Dashboard</h1>
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