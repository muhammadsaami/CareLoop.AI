import { cn } from '@/lib/cn'
import { ENUM_LABELS, type CheckInQuestion as CheckInQuestionModel, type SymptomChange } from '@/types/api'

/** Option labels for the closed answer sets. Falls back to a humanised value. */
const OPTION_LABELS: Record<string, Record<string, string>> = {
  general_wellbeing: ENUM_LABELS.wellbeing,
  condition_change: ENUM_LABELS.conditionChange,
  warning_symptoms: ENUM_LABELS.symptomChange,
}

function optionLabel(questionKey: string, value: string): string {
  return OPTION_LABELS[questionKey]?.[value] ?? value.replace(/_/g, ' ')
}

export interface CheckInQuestionProps {
  question: CheckInQuestionModel
  /** `general_wellbeing`, `condition_change` or `warning_symptoms`. */
  answer: string | undefined
  onAnswer: (value: string) => void
  /** For `sourced_from_warning_symptoms`: description keyed by symptom id. */
  symptomLabels?: Record<string, string>
  /** Current change keyed by symptom id, for the multi_select variant. */
  symptomAnswers?: Record<string, SymptomChange>
  onSymptomAnswer?: (symptomId: string, change: SymptomChange) => void
  error?: string
  disabled?: boolean
}

/**
 * Renders ONE question from the server's question set.
 *
 * Two properties of this component are load-bearing for safety:
 *
 *  1. IT RENDERS THE SERVER'S QUESTIONS. The backend serves a versioned
 *     question set via `GET /checkins/questions` so that the answer space is a
 *     closed set of enum codes. This component never invents, reorders, adds or
 *     rewords a question. If the question set changes, the check-in changes.
 *
 *  2. THE PATIENT CANNOT TYPE AN ANSWER. Every control is a radio group or a
 *     select over a fixed option list. There is no text field, because free
 *     text cannot be evaluated as arithmetic on stored facts — which is the
 *     entire reason the red-flag rules are safe.
 *
 *     The one exception is `sourced_from_warning_symptoms`, where the patient
 *     picks a state for each of THEIR OWN stored symptoms. The symptom
 *     descriptions come from the backend and are never patient-typed, so the
 *     patient still never types anything.
 */
export function CheckInQuestion({
  question,
  answer,
  onAnswer,
  symptomLabels,
  symptomAnswers,
  onSymptomAnswer,
  error,
  disabled = false,
}: CheckInQuestionProps) {
  const isSymptomQuestion = question.sourced_from_warning_symptoms
  const isMulti = question.answer_type === 'multi_select'

  return (
    <fieldset
      className="rounded-[var(--radius-card)] border border-line bg-surface p-4 shadow-soft sm:p-5"
      disabled={disabled}
      aria-describedby={error ? `${question.key}-error` : undefined}
    >
      <legend className="text-[15px] font-semibold leading-snug text-ink-900">
        {question.prompt}
      </legend>

      {isSymptomQuestion ? (
        <SymptomAnswerGroup
          question={question}
          symptomLabels={symptomLabels ?? {}}
          answers={symptomAnswers ?? {}}
          onAnswer={onSymptomAnswer}
          error={error}
        />
      ) : isMulti ? (
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {question.options.map((option) => {
            const selected = answer === option
            return (
              <label
                key={option}
                className={cn(
                  'flex cursor-pointer items-center gap-2.5 rounded-[var(--radius-control)] border px-3.5 py-2.5 text-sm transition-colors',
                  selected
                    ? 'border-brand-600 bg-brand-50 font-medium text-brand-900'
                    : 'border-line-strong text-ink-700 hover:border-ink-300 hover:bg-ink-50',
                  disabled && 'cursor-not-allowed opacity-60',
                )}
              >
                <input
                  type="radio"
                  name={question.key}
                  value={option}
                  checked={selected}
                  onChange={() => onAnswer(option)}
                  className="size-4 accent-[var(--color-brand-700)]"
                />
                {optionLabel(question.key, option)}
              </label>
            )
          })}
        </div>
      ) : (
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {question.options.map((option) => {
            const selected = answer === option
            return (
              <label
                key={option}
                className={cn(
                  'flex cursor-pointer items-center gap-2.5 rounded-[var(--radius-control)] border px-3.5 py-2.5 text-sm transition-colors',
                  selected
                    ? 'border-brand-600 bg-brand-50 font-medium text-brand-900'
                    : 'border-line-strong text-ink-700 hover:border-ink-300 hover:bg-ink-50',
                  disabled && 'cursor-not-allowed opacity-60',
                )}
              >
                <input
                  type="radio"
                  name={question.key}
                  value={option}
                  checked={selected}
                  onChange={() => onAnswer(option)}
                  className="size-4 accent-[var(--color-brand-700)]"
                />
                {optionLabel(question.key, option)}
              </label>
            )
          })}
        </div>
      )}

      {error ? (
        <p id={`${question.key}-error`} role="alert" className="mt-3 text-[13px] font-medium text-danger-700">
          {error}
        </p>
      ) : null}
    </fieldset>
  )
}

interface SymptomGroupProps {
  question: CheckInQuestionModel
  symptomLabels: Record<string, string>
  answers: Record<string, SymptomChange>
  onAnswer?: (symptomId: string, change: SymptomChange) => void
  error?: string
}

/**
 * One row per stored warning symptom.
 *
 * The severity badge is shown so the patient knows which symptoms their care
 * team flagged as most serious — it comes from the backend's stored record, and
 * it is a fact, not advice.
 */
function SymptomAnswerGroup({ question, symptomLabels, answers, onAnswer, error }: SymptomGroupProps) {
  const symptomIds = Object.keys(symptomLabels)
  const changes = question.options.filter((option) => option !== 'absent')

  return (
    <div className="mt-3 space-y-3">
      {error ? (
        <p id={`${question.key}-error`} role="alert" className="text-[13px] font-medium text-danger-700">
          {error}
        </p>
      ) : null}
      {symptomIds.length === 0 ? (
        <p className="rounded-[var(--radius-control)] border border-line bg-ink-50 px-3.5 py-3 text-[13px] text-ink-500">
          No warning symptoms are recorded on your discharge instructions yet. Add them in Documents
          and they will appear here.
        </p>
      ) : null}

      {symptomIds.map((symptomId) => (
        <div
          key={symptomId}
          className="rounded-[var(--radius-control)] border border-line bg-surface-muted p-3.5"
        >
          <p className="text-sm font-medium text-ink-800">{symptomLabels[symptomId]}</p>
          <div className="mt-2.5 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => onAnswer?.(symptomId, 'absent')}
              aria-pressed={answers[symptomId] === 'absent'}
              className={cn(
                'rounded-full border px-3 py-1.5 text-[13px] font-medium transition-colors',
                answers[symptomId] === 'absent'
                  ? 'border-success-600 bg-success-50 text-success-700'
                  : 'border-line-strong text-ink-600 hover:bg-surface',
              )}
            >
              Not happening
            </button>
            {changes.map((change) => (
              <button
                key={change}
                type="button"
                onClick={() => onAnswer?.(symptomId, change as SymptomChange)}
                aria-pressed={answers[symptomId] === change}
                className={cn(
                  'rounded-full border px-3 py-1.5 text-[13px] font-medium transition-colors',
                  answers[symptomId] === change
                    ? change === 'worse'
                      ? 'border-danger-600 bg-danger-50 text-danger-700'
                      : 'border-warning-600 bg-warning-50 text-warning-700'
                    : 'border-line-strong text-ink-600 hover:bg-surface',
                )}
              >
                {optionLabel(question.key, change)}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
