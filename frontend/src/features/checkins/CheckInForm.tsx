import { useMemo, useState, type ReactNode } from 'react'
import { useForm } from 'react-hook-form'
import { Alert } from '@/components/Alert'
import { Button } from '@/components/Button'
import { CheckInQuestion } from '@/components/CheckInQuestion'
import { Icon } from '@/components/Icon'
import { LoadingState } from '@/components/LoadingState'
import { ReviewBanner } from '@/components/ReviewBanner'
import { Badge } from '@/components/Badge'
import { ErrorState } from '@/components/ErrorState'
import { useCheckinQuestions, useCheckinHistory, useSubmitCheckin, useWarningSymptoms } from '@/hooks/usePatientData'
import { formatDate, formatDateTime, todayIso } from '@/lib/format'
import { ApiError } from '@/lib/apiClient'
import { ENUM_LABELS, type DailyCheckIn, type SymptomChange } from '@/types/api'

/**
 * The daily check-in.
 *
 * THE QUESTION SET IS DATA. It is fetched from
 * `GET /patients/{id}/checkins/questions` and rendered as-is. This file
 * contains no question text, no answer options and no red-flag logic, because
 * the backend deliberately keeps the answer space a closed set of enum codes
 * that its rules can evaluate as arithmetic on stored facts. Adding a question
 * here would put a question in the UI that the safety rules know nothing about.
 *
 * Answers are therefore a fixed shape: wellbeing, condition change, and one
 * state per stored warning symptom.
 */
export function CheckInForm({ patientId, timezone }: { patientId: string; timezone: string }) {
  const questions = useCheckinQuestions(patientId)
  const symptoms = useWarningSymptoms(patientId)
  const submit = useSubmitCheckin()

  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [symptomAnswers, setSymptomAnswers] = useState<Record<string, SymptomChange>>({})
  const [submitted, setSubmitted] = useState<DailyCheckIn | null>(null)

  const form = useForm<{ date: string }>({
    defaultValues: { date: todayIso(timezone) },
  })

  /** symptom_id -> description, so the UI can name each stored symptom. */
  const symptomLabels = useMemo(() => {
    const labels: Record<string, string> = {}
    for (const symptom of symptoms.data ?? []) labels[symptom.id] = symptom.description
    return labels
  }, [symptoms.data])

  if (questions.isError) {
    return <ErrorState error={questions.error} onRetry={() => void questions.refetch()} />
  }

  if (questions.isLoading) {
    return <LoadingState label="Loading your check-in questions" />
  }

  const questionSet = questions.data
  if (!questionSet || questionSet.questions.length === 0) {
    return (
      <Alert tone="warning" title="No check-in questions are available">
        CareLoop could not load the check-in questions. Try again shortly, or contact your care
        team if this keeps happening.
      </Alert>
    )
  }

  async function onSubmit() {
    submit.mutate(
      {
        patientId,
        body: {
          date: form.getValues('date'),
          timezone,
          general_wellbeing: (answers.general_wellbeing ?? null) as never,
          condition_change: (answers.condition_change ?? null) as never,
          warning_symptoms: Object.entries(symptomAnswers).map(([symptom_id, change]) => ({
            symptom_id,
            change,
          })),
        },
      },
      {
        onSuccess: (result) => {
          setSubmitted(result)
          setAnswers({})
          setSymptomAnswers({})
        },
      },
    )
  }

  if (submitted) {
    return <CheckInResult checkin={submitted} onAnother={() => setSubmitted(null)} />
  }

  const error = submit.error instanceof ApiError ? submit.error : null

  return (
    <div className="space-y-4">
      <form
        onSubmit={form.handleSubmit(() => void onSubmit())}
        className="space-y-4"
        noValidate
      >
        {questionSet.questions.map((question) => (
          <CheckInQuestion
            key={question.key}
            question={question}
            disabled={submit.isPending}
            answer={answers[question.key]}
            onAnswer={(value) => setAnswers((current) => ({ ...current, [question.key]: value }))}
            symptomLabels={question.sourced_from_warning_symptoms ? symptomLabels : undefined}
            symptomAnswers={question.sourced_from_warning_symptoms ? symptomAnswers : undefined}
            onSymptomAnswer={
              question.sourced_from_warning_symptoms
                ? (symptomId, change) =>
                    setSymptomAnswers((current) => ({ ...current, [symptomId]: change }))
                : undefined
            }
          />
        ))}

        {error ? (
          <Alert tone="danger" title="Your check-in was not saved">
            {error.userMessage}
          </Alert>
        ) : null}

        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button
            type="button"
            variant="ghost"
            onClick={() => {
              setAnswers({})
              setSymptomAnswers({})
            }}
            disabled={submit.isPending}
          >
            Clear
          </Button>
          <Button type="submit" size="lg" loading={submit.isPending}>
            Submit check-in
          </Button>
        </div>
      </form>

      <p className="flex items-start gap-2 text-[12px] leading-relaxed text-ink-400">
        <Icon name="shield" size={14} className="mt-0.5" />
        These questions only offer fixed choices from your own discharge instructions. You cannot
        type free text, and CareLoop does not interpret what you write.
      </p>
    </div>
  )
}

/**
 * Post-submit confirmation.
 *
 * `patient_message` is the backend's own copy and is shown verbatim, because
 * whether a care team was alerted is a fact this app must not paraphrase into
 * something more or less reassuring than what the rules actually decided.
 */
function CheckInResult({ checkin, onAnother }: { checkin: DailyCheckIn; onAnother: () => void }) {
  return (
    <div className="space-y-4">
      <OutcomePanel tone={checkin.status === 'escalated' ? 'danger' : 'brand'}>
        <div className="flex flex-col items-center py-4 text-center">
          <span
            className={
              checkin.status === 'escalated'
                ? 'flex size-12 items-center justify-center rounded-full bg-danger-50 text-danger-600'
                : 'flex size-12 items-center justify-center rounded-full bg-brand-50 text-brand-600'
            }
          >
            <Icon name={checkin.status === 'escalated' ? 'alert' : 'check'} size={24} />
          </span>
          <p className="mt-3 text-[15px] font-semibold text-ink-900">Check-in recorded</p>
          <p className="mt-1.5 max-w-md text-sm leading-relaxed text-ink-600">
            {checkin.patient_message}
          </p>
        </div>
      </OutcomePanel>

      {checkin.needs_review ? (
        <ReviewBanner reason={checkin.review_reason} />
      ) : null}

      {checkin.escalations.length > 0 ? (
        <div className="space-y-2">
          {checkin.escalations.map((escalation) => (
            <div
              key={escalation.id}
              className="rounded-[var(--radius-card)] border border-line bg-surface p-4"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-medium text-ink-800">
                  {ENUM_LABELS.escalationCategory[escalation.category] ?? escalation.category}
                </p>
                <Badge tone="warning" size="sm" dot>
                  {ENUM_LABELS.escalationStatus[escalation.status] ?? escalation.status}
                </Badge>
              </div>
              <p className="mt-1.5 text-[13px] text-ink-500">
                Raised {formatDateTime(escalation.created_at)}
              </p>
            </div>
          ))}
        </div>
      ) : null}

      <div className="flex justify-end">
        <Button variant="secondary" onClick={onAnother}>
          Done
        </Button>
      </div>
    </div>
  )
}

/**
 * Tinted panel for a submitted check-in.
 *
 * Named `OutcomePanel` rather than `Card` so it cannot be confused with the
 * shared `Card` in `@/components/Card`, which this module deliberately does not
 * import: the shared card is white, and this panel has to carry the outcome
 * colour across the whole block.
 */
function OutcomePanel({ children, tone }: { children: ReactNode; tone: 'danger' | 'brand' }) {
  return (
    <div
      className={
        tone === 'danger'
          ? 'rounded-[var(--radius-card)] border border-danger-200 bg-danger-50'
          : 'rounded-[var(--radius-card)] border border-brand-200 bg-brand-50'
      }
    >
      {children}
    </div>
  )
}

/** Read-only history of past check-ins. */
export function CheckInHistory({ patientId }: { patientId: string }) {
  const history = useCheckinHistory(patientId)

  if (history.isLoading) return <LoadingState label="Loading check-in history" />
  if (history.isError) return <ErrorState error={history.error} onRetry={() => void history.refetch()} compact />

  const items = history.data?.items ?? []
  if (items.length === 0) {
    return (
      <p className="rounded-[var(--radius-card)] border border-dashed border-line-strong px-4 py-6 text-center text-sm text-ink-500">
        No check-ins recorded yet.
      </p>
    )
  }

  return (
    <ol className="space-y-2">
      {items.map((item) => (
        <li
          key={item.id}
          className="flex flex-wrap items-center justify-between gap-2 rounded-[var(--radius-card)] border border-line bg-surface px-4 py-3"
        >
          <div className="min-w-0">
            <p className="text-sm font-medium text-ink-800" data-numeric>
              {formatDate(item.date)}
            </p>
            {item.review_reason ? (
              <p className="mt-0.5 text-[12px] text-warning-700">{item.review_reason}</p>
            ) : null}
          </div>
          <div className="flex items-center gap-2">
            {item.escalation_count > 0 ? (
              <Badge tone="danger" size="sm">
                {item.escalation_count} alert{item.escalation_count === 1 ? '' : 's'}
              </Badge>
            ) : null}
            <Badge
              tone={
                item.status === 'escalated'
                  ? 'danger'
                  : item.status === 'needs_review'
                    ? 'warning'
                    : 'success'
              }
              size="sm"
              dot
            >
              {ENUM_LABELS.checkInStatus[item.status] ?? item.status}
            </Badge>
          </div>
        </li>
      ))}
    </ol>
  )
}
