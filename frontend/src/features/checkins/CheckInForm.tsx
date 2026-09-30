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
import {
  useCheckinQuestions,
  useCheckinHistory,
  useSubmitCheckin,
  useWarningSymptoms,
} from '@/hooks/usePatientData'
import { formatDate, formatDateTime, todayIso } from '@/lib/format'
import { ApiError } from '@/lib/apiClient'
import { cn } from '@/lib/cn'
import {
  ENUM_LABELS,
  type CheckInQuestion as CheckInQuestionModel,
  type DailyCheckIn,
  type SymptomChange,
  type WarningSymptom,
} from '@/types/api'

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
 *
 * PRESENTATION ONLY. The three known question keys get a premium, sectioned
 * layout (overall feeling -> recovery -> warning symptoms). Any question the
 * backend adds beyond those keys is still rendered, unchanged, via the generic
 * `CheckInQuestion` renderer, so the question set can never silently lose a
 * question because this screen restructured. Sections, headings and option
 * lists are all derived from the server's question set and the patient's own
 * warning symptoms - nothing is hard-coded.
 */
export function CheckInForm({ patientId, timezone }: { patientId: string; timezone: string }) {
  const questions = useCheckinQuestions(patientId)
  const symptomsQuery = useWarningSymptoms(patientId)
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
    for (const symptom of symptomsQuery.data ?? []) labels[symptom.id] = symptom.description
    return labels
  }, [symptomsQuery.data])

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

  const overallQuestion =
    questionSet.questions.find((question) => question.key === 'general_wellbeing') ?? null
  const recoveryQuestion =
    questionSet.questions.find((question) => question.key === 'condition_change') ?? null
  const symptomsQuestion =
    questionSet.questions.find((question) => question.key === 'warning_symptoms') ?? null

  // Any question outside the three structured sections still renders as-is.
  const KNOWN_KEYS = new Set(['general_wellbeing', 'condition_change', 'warning_symptoms'])
  const extraQuestions = questionSet.questions.filter((question) => !KNOWN_KEYS.has(question.key))

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
  const isPending = submit.isPending
  const symptoms = symptomsQuery.data ?? []

  function setAnswer(key: string, value: string) {
    setAnswers((current) => ({ ...current, [key]: value }))
  }
  function setSymptomAnswer(symptomId: string, change: SymptomChange) {
    setSymptomAnswers((current) => ({ ...current, [symptomId]: change }))
  }
  function onClear() {
    setAnswers({})
    setSymptomAnswers({})
  }

  const allSymptomsAnswered =
    symptoms.length === 0 || symptoms.every((symptom) => symptomAnswers[symptom.id] != null)

  const steps = [overallQuestion, recoveryQuestion, symptomsQuestion]
    .filter((question): question is CheckInQuestionModel => question !== null)
    .map((question) => ({
      key: question.key,
      label:
        question.key === 'general_wellbeing'
          ? 'Overall'
          : question.key === 'condition_change'
            ? 'Recovery'
            : 'Symptoms',
      done:
        question.key === 'warning_symptoms'
          ? allSymptomsAnswered
          : answers[question.key] != null,
    }))

  const firstOpenIndex = steps.findIndex((step) => !step.done)
  const stepsComplete = firstOpenIndex === -1
  const currentStep = stepsComplete ? steps.length : firstOpenIndex + 1

  // Same rule the backend enforces in `DailyCheckInCreate.validate_answer_set`:
  // at least one answer. The submit button stays disabled until then.
  const canSubmit =
    (overallQuestion ? answers[overallQuestion.key] != null : false) ||
    (recoveryQuestion ? answers[recoveryQuestion.key] != null : false) ||
    Object.keys(symptomAnswers).length > 0

  const sections: Array<{
    key: string
    index: number
    kicker: string
    heading: string
    supporting?: string
    body: ReactNode
  }> = []

  let sectionNumber = 0
  if (overallQuestion) {
    sectionNumber += 1
    sections.push({
      key: overallQuestion.key,
      index: sectionNumber,
      kicker: 'Overall feeling',
      heading: overallQuestion.prompt,
      body: (
        <OptionGrid
          name={overallQuestion.key}
          question={overallQuestion}
          answer={answers[overallQuestion.key]}
          onAnswer={(value) => setAnswer(overallQuestion.key, value)}
          disabled={isPending}
        />
      ),
    })
  }
  if (recoveryQuestion) {
    sectionNumber += 1
    sections.push({
      key: recoveryQuestion.key,
      index: sectionNumber,
      kicker: 'Recovery',
      heading: recoveryQuestion.prompt,
      body: (
        <OptionGrid
          name={recoveryQuestion.key}
          question={recoveryQuestion}
          answer={answers[recoveryQuestion.key]}
          onAnswer={(value) => setAnswer(recoveryQuestion.key, value)}
          disabled={isPending}
        />
      ),
    })
  }
  if (symptomsQuestion) {
    sectionNumber += 1
    sections.push({
      key: symptomsQuestion.key,
      index: sectionNumber,
      kicker: 'Warning symptoms',
      heading: 'Your warning symptoms',
      supporting: symptomsQuestion.prompt,
      body: (
        <SymptomQuestionBody
          question={symptomsQuestion}
          symptoms={symptoms}
          answers={symptomAnswers}
          onAnswer={setSymptomAnswer}
          loading={symptomsQuery.isLoading}
          error={symptomsQuery.isError ? symptomsQuery.error : null}
          onRetry={() => void symptomsQuery.refetch()}
          disabled={isPending}
        />
      ),
    })
  }

  return (
    <div className="space-y-5">
      <WelcomeCard />

      <form
        onSubmit={form.handleSubmit(() => void onSubmit())}
        className="space-y-6"
        noValidate
      >
        <CheckInStepper steps={steps} current={currentStep} complete={stepsComplete} />

        {sections.map((section) => (
          <SectionCard
            key={section.key}
            index={section.index}
            kicker={section.kicker}
            heading={section.heading}
            supporting={section.supporting}
          >
            {section.body}
          </SectionCard>
        ))}

        {extraQuestions.length > 0 ? (
          <div className="space-y-4">
            {extraQuestions.map((question) => (
              <CheckInQuestion
                key={question.key}
                question={question}
                disabled={isPending}
                answer={answers[question.key]}
                onAnswer={(value) => setAnswer(question.key, value)}
                symptomLabels={
                  question.sourced_from_warning_symptoms ? symptomLabels : undefined
                }
                symptomAnswers={
                  question.sourced_from_warning_symptoms ? symptomAnswers : undefined
                }
                onSymptomAnswer={
                  question.sourced_from_warning_symptoms ? setSymptomAnswer : undefined
                }
              />
            ))}
          </div>
        ) : null}

        {error ? (
          <Alert tone="danger" title="Your check-in was not saved">
            {error.userMessage}
          </Alert>
        ) : null}

        <SubmitBar canSubmit={canSubmit} isPending={isPending} onClear={onClear} />
      </form>

      <p className="flex items-start gap-2 text-[12px] leading-relaxed text-ink-400">
        <Icon name="shield" size={14} className="mt-0.5 shrink-0" />
        These questions only offer fixed choices from your own discharge instructions. You cannot
        type free text, and CareLoop does not interpret what you write.
      </p>
    </div>
  )
}

/** Small premium welcome strip with the CareLoop AI mark. */
function WelcomeCard() {
  return (
    <section className="flex items-center gap-4 rounded-[var(--radius-card)] border border-brand-100 bg-brand-50/60 p-5 sm:p-6">
      <span
        aria-hidden="true"
        className="flex size-11 shrink-0 items-center justify-center rounded-2xl bg-linear-to-br from-brand-600 to-brand-800 text-white shadow-soft"
      >
        <Icon name="sparkle" size={20} />
      </span>
      <div className="min-w-0">
        <p className="text-[15px] font-semibold tracking-tight text-ink-950">
          How are you feeling today?
        </p>
        <p className="mt-0.5 text-sm text-ink-500">Let&apos;s take a quick check-in together.</p>
      </div>
    </section>
  )
}

interface StepModel {
  key: string
  label: string
  done: boolean
}

/**
 * Visual-only progress across the three structured sections. It never affects
 * what is submitted or asked; it only mirrors the answers already given.
 */
function CheckInStepper({
  steps,
  current,
  complete,
}: {
  steps: StepModel[]
  current: number
  complete: boolean
}) {
  const count = steps.length
  const segments = Math.max(1, count - 1)
  // The connecting track runs between the centres of the first and last dot.
  const left = 100 / (count * 2)
  const trackWidth = 100 - left * 2
  const fillWidth = ((current - 1) / segments) * trackWidth

  const labelText = complete ? 'All steps answered' : `Step ${current} of ${count}`

  return (
    <section
      role="progressbar"
      aria-valuemin={1}
      aria-valuemax={count}
      aria-valuenow={current}
      aria-label={`Check-in progress: ${labelText}`}
      className="rounded-[var(--radius-card)] border border-line bg-surface px-5 py-4 shadow-soft sm:px-6"
    >
      <p className="text-center text-[13px] font-medium text-ink-600" data-numeric>
        {labelText}
      </p>
      <div className="relative mx-auto mt-3 max-w-md">
        <div
          aria-hidden="true"
          className="absolute top-[7px] h-0.5 rounded-full bg-ink-200"
          style={{ left: `${left}%`, width: `${trackWidth}%` }}
        />
        <div
          aria-hidden="true"
          className="absolute top-[7px] h-0.5 rounded-full bg-brand-600 transition-[width] duration-300"
          style={{ left: `${left}%`, width: `${fillWidth}%` }}
        />
        <div
          className="relative grid"
          style={{ gridTemplateColumns: `repeat(${count}, minmax(0, 1fr))` }}
        >
          {steps.map((step, index) => {
            const isActive = !step.done && index === current - 1
            return (
              <div key={step.key} className="flex flex-col items-center gap-1.5">
                <span
                  aria-hidden="true"
                  className={cn(
                    'size-3.5 rounded-full border transition-colors',
                    step.done
                      ? 'border-brand-600 bg-brand-600'
                      : isActive
                        ? 'border-brand-600 bg-brand-50 ring-4 ring-brand-600/20'
                        : 'border-ink-300 bg-surface',
                  )}
                />
                <span
                  className={cn(
                    'text-[11px] font-medium',
                    step.done || isActive ? 'text-ink-900' : 'text-ink-400',
                  )}
                >
                  {step.label}
                </span>
              </div>
            )
          })}
        </div>
      </div>
    </section>
  )
}

function SectionCard({
  index,
  kicker,
  heading,
  supporting,
  children,
}: {
  index: number
  kicker: string
  heading: string
  supporting?: string
  children: ReactNode
}) {
  return (
    <section className="rounded-[var(--radius-card)] border border-line bg-surface p-5 shadow-soft sm:p-6">
      <div className="flex items-center gap-2">
        <span
          aria-hidden="true"
          className="flex size-6 shrink-0 items-center justify-center rounded-full bg-brand-50 text-[12px] font-semibold text-brand-700 ring-1 ring-inset ring-brand-200"
        >
          {index}
        </span>
        <p className="text-[13px] font-semibold uppercase tracking-wider text-ink-500">{kicker}</p>
      </div>
      <h2 className="mt-2.5 text-lg font-semibold tracking-tight text-ink-950">{heading}</h2>
      {supporting ? (
        <p className="mt-1 max-w-2xl text-sm leading-relaxed text-ink-500">{supporting}</p>
      ) : null}
      <div className="mt-4">{children}</div>
    </section>
  )
}

/** Option labels for the closed answer sets. Falls back to a humanised value. */
function optionLabel(questionKey: string, value: string): string {
  const map =
    questionKey === 'general_wellbeing'
      ? ENUM_LABELS.wellbeing
      : questionKey === 'condition_change'
        ? ENUM_LABELS.conditionChange
        : null
  return map?.[value as keyof typeof map] ?? value.replace(/_/g, ' ')
}

interface OptionCardProps {
  name: string
  value: string
  label: string
  selected: boolean
  onSelect: () => void
  disabled?: boolean
  /** Selected-state classes; defaults to the teal brand treatment. */
  selectedClass?: string
}

/** A selectable option that fills the whole card and keeps radio semantics. */
function OptionCard({
  name,
  value,
  label,
  selected,
  onSelect,
  disabled,
  selectedClass,
}: OptionCardProps) {
  return (
    <label
      className={cn(
        'flex min-h-11 cursor-pointer items-center gap-2.5 rounded-xl border px-3.5 py-2.5 text-sm font-medium transition-colors',
        'has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-brand-600/40',
        selected
          ? cn('border-brand-600 bg-brand-50 text-brand-800', selectedClass)
          : 'border-line-strong text-ink-700 hover:border-ink-300 hover:bg-ink-50',
        disabled && 'cursor-not-allowed opacity-60',
      )}
    >
      <input
        type="radio"
        name={name}
        value={value}
        checked={selected}
        onChange={onSelect}
        disabled={disabled}
        className="sr-only"
      />
      <RadioIndicator selected={selected} />
      <span className="min-w-0">{label}</span>
    </label>
  )
}

/** The same sized radio glyph every option card uses; `current` supplies colour. */
function RadioIndicator({ selected }: { selected: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'flex size-5 shrink-0 items-center justify-center rounded-full border-2 transition-colors',
        selected ? 'border-current' : 'border-ink-300',
      )}
    >
      <span
        className={cn(
          'size-2.5 rounded-full transition-colors',
          selected ? 'bg-current' : 'bg-transparent',
        )}
      />
    </span>
  )
}

/** A single-select grid for the wellbeing and recovery questions. */
function OptionGrid({
  question,
  name,
  answer,
  onAnswer,
  disabled,
}: {
  question: CheckInQuestionModel
  name: string
  answer: string | undefined
  onAnswer: (value: string) => void
  disabled: boolean
}) {
  return (
    <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
      {question.options.map((option) => {
        const selected = answer === option
        return (
          <OptionCard
            key={option}
            name={name}
            value={option}
            label={optionLabel(question.key, option)}
            selected={selected}
            onSelect={() => onAnswer(option)}
            disabled={disabled}
          />
        )
      })}
    </div>
  )
}

function SymptomQuestionBody({
  question,
  symptoms,
  answers,
  onAnswer,
  loading,
  error,
  onRetry,
  disabled,
}: {
  question: CheckInQuestionModel
  symptoms: WarningSymptom[]
  answers: Record<string, SymptomChange>
  onAnswer: (symptomId: string, change: SymptomChange) => void
  loading: boolean
  error: unknown
  onRetry: () => void
  disabled: boolean
}) {
  if (loading) {
    return (
      <div className="space-y-3">
        {[0, 1].map((item) => (
          <div key={item} className="h-24 animate-pulse rounded-xl bg-ink-100" />
        ))}
      </div>
    )
  }

  if (error) {
    return (
      <div className="rounded-xl border border-line bg-surface-muted px-4 py-4">
        <p className="flex items-center gap-2 text-sm font-medium text-ink-700">
          <Icon name="alert" size={16} className="shrink-0 text-ink-400" />
          Your warning symptoms could not be loaded.
        </p>
        <p className="mt-1 text-[13px] leading-relaxed text-ink-500">
          You can still complete the rest of your check-in. Try loading them again if you like.
        </p>
        <div className="mt-3">
          <Button variant="ghost" size="sm" onClick={onRetry} disabled={disabled}>
            Try again
          </Button>
        </div>
      </div>
    )
  }

  if (symptoms.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-line-strong bg-surface-muted px-4 py-6 text-center">
        <p className="text-sm font-medium text-ink-700">No warning symptoms recorded</p>
        <p className="mx-auto mt-1 max-w-md text-[13px] leading-relaxed text-ink-500">
          CareLoop did not find any warning symptoms in your current discharge information.
        </p>
      </div>
    )
  }

  // The change codes are the server's answer set; only the "absent" label is
  // the plain-language copy the check-in has always shown.
  const changeOptions = question.options.map((code) => ({
    code,
    label:
      code === 'absent'
        ? 'Not happening'
        : (ENUM_LABELS.symptomChange[code as SymptomChange] ?? code.replace(/_/g, ' ')),
  }))

  return (
    <div className="space-y-3">
      {symptoms.map((symptom) => (
        <div key={symptom.id} className="rounded-xl border border-line bg-surface-muted p-4">
          <fieldset disabled={disabled || undefined}>
            <legend className="sr-only">{symptom.description}</legend>
            <p className="text-sm font-semibold text-ink-900">{symptom.description}</p>
            <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
              {changeOptions.map((option) => (
                <OptionCard
                  key={option.code}
                  name={`symptom-${symptom.id}`}
                  value={option.code}
                  label={option.label}
                  selected={answers[symptom.id] === option.code}
                  onSelect={() => onAnswer(symptom.id, option.code as SymptomChange)}
                  disabled={disabled}
                  selectedClass={
                    option.code === 'absent'
                      ? 'border-success-600 bg-success-50 text-success-700'
                      : option.code === 'worse'
                        ? 'border-danger-600 bg-danger-50 text-danger-700'
                        : 'border-warning-600 bg-warning-50 text-warning-700'
                  }
                />
              ))}
            </div>
          </fieldset>
        </div>
      ))}
    </div>
  )
}

/** Sticky action bar that clears the shell's mobile bottom navigation. */
function SubmitBar({
  canSubmit,
  isPending,
  onClear,
}: {
  canSubmit: boolean
  isPending: boolean
  onClear: () => void
}) {
  return (
    <div className="sticky bottom-[calc(4rem_+_env(safe-area-inset-bottom))] z-[5] md:bottom-8">
      <div className="flex flex-col gap-4 rounded-[var(--radius-card)] border border-line bg-surface p-4 shadow-soft sm:p-5 md:flex-row md:items-center md:justify-between">
        <p className="flex max-w-xl items-start gap-2 text-[12px] leading-relaxed text-ink-500">
          <Icon name="shield" size={14} className="mt-0.5 shrink-0 text-brand-700" />
          <span>
            Your responses are saved securely and shared with your care team only according to your
            configured access.
          </span>
        </p>
        <div className="flex shrink-0 items-center justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClear} disabled={isPending}>
            Clear
          </Button>
          <Button type="submit" size="lg" loading={isPending} disabled={!canSubmit}>
            Submit check-in
          </Button>
        </div>
      </div>
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

      {checkin.needs_review ? <ReviewBanner reason={checkin.review_reason} /> : null}

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
  if (history.isError)
    return <ErrorState error={history.error} onRetry={() => void history.refetch()} compact />

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