import { useState } from 'react'
import { Alert } from '@/components/Alert'
import { Badge } from '@/components/Badge'
import { Button } from '@/components/Button'
import { Card, CardHeader } from '@/components/Card'
import { EmptyState } from '@/components/EmptyState'
import { errorMessage } from '@/components/ErrorState'
import { Icon } from '@/components/Icon'
import { Input } from '@/components/Input'
import { LoadingState } from '@/components/LoadingState'
import { Modal } from '@/components/Modal'
import { Select } from '@/components/Select'
import {
  useCreateWarningSymptom,
  useDeleteWarningSymptom,
  useWarningSymptoms,
} from '@/hooks/usePatientData'
import { ENUM_LABELS, type SymptomSeverity, type WarningSymptom } from '@/types/api'

const SEVERITY_OPTIONS = (
  Object.entries(ENUM_LABELS.symptomSeverity) as Array<[SymptomSeverity, string]>
).map(([value, label]) => ({ value, label }))

const SEVERITY_TONE: Record<SymptomSeverity, 'neutral' | 'warning' | 'danger'> = {
  low: 'neutral',
  medium: 'warning',
  high: 'danger',
  critical: 'danger',
}

/**
 * The warning symptoms recorded on this patient's discharge instructions.
 *
 * These are not free-text notes the patient keeps for themselves. They are the
 * stored facts that `GET /checkins/questions` builds the daily check-in's
 * symptom question from, and that the backend's red-flag rules evaluate
 * arithmetically. So the copy says "copy it from your paperwork" rather than
 * inviting the patient to describe how they feel — the patient is never asked
 * to author the thing the safety rules depend on.
 */
export function WarningSymptomManager({ patientId }: { patientId: string }) {
  const symptoms = useWarningSymptoms(patientId)
  const [adding, setAdding] = useState(false)

  const items = symptoms.data ?? []

  return (
    <Card padding="md">
      <CardHeader
        title="Warning symptoms"
        description="Copied from your discharge instructions"
        icon={<Icon name="alert" size={18} />}
        action={
          <Button
            variant="subtle"
            size="sm"
            leadingIcon={<Icon name="plus" size={14} />}
            onClick={() => setAdding(true)}
          >
            Add
          </Button>
        }
      />

      <p className="mt-3 text-[13px] leading-relaxed text-ink-500">
        Your daily check-in asks about each of these, and your care team is alerted if one changes.
      </p>

      {symptoms.isLoading ? (
        <div className="mt-3">
          <LoadingState label="Loading warning symptoms" />
        </div>
      ) : items.length === 0 ? (
        <div className="mt-3">
          <EmptyState
            size="sm"
            icon={<Icon name="alert" size={20} />}
            title="None recorded"
            description="Add the warning symptoms listed on your paperwork so your check-in can ask about them."
          />
        </div>
      ) : (
        <ul className="mt-3 space-y-2">
          {items.map((symptom) => (
            <WarningSymptomRow
              key={symptom.id}
              symptom={symptom}
              patientId={patientId}
            />
          ))}
        </ul>
      )}

      {symptoms.isError ? (
        <Alert tone="danger" className="mt-3" title="Could not load warning symptoms">
          {errorMessage(symptoms.error)}
        </Alert>
      ) : null}

      {adding ? <AddSymptomDialog patientId={patientId} onClose={() => setAdding(false)} /> : null}
    </Card>
  )
}

function WarningSymptomRow({
  symptom,
  patientId,
}: {
  symptom: WarningSymptom
  patientId: string
}) {
  const remove = useDeleteWarningSymptom()
  const [confirming, setConfirming] = useState(false)

  return (
    <li className="flex items-start justify-between gap-2 rounded-[var(--radius-control)] border border-line bg-surface-muted px-3 py-2.5">
      <div className="min-w-0">
        <p className="text-[13px] font-medium text-ink-800">{symptom.description}</p>
        <div className="mt-1">
          <Badge tone={SEVERITY_TONE[symptom.severity]} size="sm">
            {ENUM_LABELS.symptomSeverity[symptom.severity] ?? symptom.severity}
          </Badge>
        </div>
      </div>
      <button
        type="button"
        onClick={() => setConfirming(true)}
        aria-label={`Remove ${symptom.description}`}
        className="shrink-0 rounded p-1.5 text-ink-400 transition-colors hover:bg-danger-50 hover:text-danger-600"
      >
        <Icon name="trash" size={15} />
      </button>

      {confirming ? (
        <Modal
          open
          onClose={() => setConfirming(false)}
          title="Remove this warning symptom?"
          size="sm"
          description="Your daily check-in will stop asking about it, and it will no longer raise an alert when it changes."
          busy={remove.isPending}
          footer={
            <>
              <Button variant="ghost" onClick={() => setConfirming(false)} disabled={remove.isPending}>
                Keep it
              </Button>
              <Button
                variant="danger"
                loading={remove.isPending}
                onClick={() =>
                  remove.mutate(
                    { patientId, symptomId: symptom.id },
                    { onSuccess: () => setConfirming(false) },
                  )
                }
              >
                Remove
              </Button>
            </>
          }
        >
          <p className="text-sm text-ink-700">{symptom.description}</p>
          {remove.error ? (
            <Alert tone="danger" className="mt-3">
              {errorMessage(remove.error)}
            </Alert>
          ) : null}
        </Modal>
      ) : null}
    </li>
  )
}

function AddSymptomDialog({ patientId, onClose }: { patientId: string; onClose: () => void }) {
  const create = useCreateWarningSymptom()
  const [description, setDescription] = useState('')
  const [severity, setSeverity] = useState<SymptomSeverity>('medium')
  const [error, setError] = useState<string | null>(null)

  function submit() {
    if (!description.trim()) {
      setError('Copy the warning symptom from your paperwork.')
      return
    }
    setError(null)
    create.mutate(
      { patientId, body: { description: description.trim(), severity } },
      { onSuccess: onClose },
    )
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Add a warning symptom"
      description="Copy the wording from your discharge paperwork so it matches exactly."
      size="sm"
      busy={create.isPending}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={create.isPending}>
            Cancel
          </Button>
          <Button onClick={submit} loading={create.isPending}>
            Add
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Input
          label="What should be watched for?"
          placeholder="e.g. Shortness of breath when lying flat"
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          error={error ?? undefined}
          required
        />
        <Select
          label="How serious did your paperwork mark it?"
          options={SEVERITY_OPTIONS}
          value={severity}
          onChange={(event) => setSeverity(event.target.value as SymptomSeverity)}
          hint="This is how your care team ranked it. It is not a judgement about you."
        />
        {create.error ? <Alert tone="danger">{errorMessage(create.error)}</Alert> : null}
      </div>
    </Modal>
  )
}
