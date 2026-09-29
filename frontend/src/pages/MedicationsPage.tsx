import { useState } from 'react'
import { Alert } from '@/components/Alert'
import { Button } from '@/components/Button'
import { EmptyState } from '@/components/EmptyState'
import { ErrorState } from '@/components/ErrorState'
import { Icon } from '@/components/Icon'
import { LoadingState } from '@/components/LoadingState'
import { MedicationCard } from '@/components/MedicationCard'
import { Modal } from '@/components/Modal'
import { PageHeader } from '@/layouts/AppShell'
import { AdherenceControl } from '@/features/medications/AdherenceControl'
import { MedicationForm } from '@/features/medications/MedicationForm'
import { useActivePatient } from '@/features/auth/useActivePatient'
import {
  useCreateMedication,
  useCreateMedicationReminder,
  useDeleteMedication,
  useMedications,
  useUpdateMedication,
} from '@/hooks/usePatientData'
import { ApiError } from '@/lib/apiClient'
import { isValidLocalTime } from '@/lib/format'
import type { Medication, MedicationCreate, MedicationUpdate, Recurrence } from '@/types/api'

type ModalState =
  | { kind: 'none' }
  | { kind: 'create' }
  | { kind: 'edit'; medication: Medication }
  | { kind: 'delete'; medication: Medication }
  | { kind: 'remind'; medication: Medication }

export function MedicationsPage() {
  const { patientId, patient } = useActivePatient()
  const medications = useMedications(patientId)
  const [modal, setModal] = useState<ModalState>({ kind: 'none' })

  if (!patientId) {
    return (
      <div className="px-4 py-6 sm:px-6 lg:px-8">
        <LoadingState label="Loading patient" />
      </div>
    )
  }

  const items = medications.data ?? []

  return (
    <div className="px-4 py-6 sm:px-6 lg:px-8">
      <PageHeader
        title="Medicines"
        description="What is on your discharge paperwork, and the reminders set up for each one."
        actions={
          <Button leadingIcon={<Icon name="plus" size={18} />} onClick={() => setModal({ kind: 'create' })}>
            Add medicine
          </Button>
        }
      />

      <Alert tone="brand" className="mb-5">
        Medicines extracted from your documents are marked for review until someone confirms them.
        Check them against your paperwork before changing anything.
      </Alert>

      {medications.isError ? (
        <ErrorState error={medications.error} onRetry={() => void medications.refetch()} />
      ) : medications.isLoading ? (
        <LoadingState label="Loading medicines" />
      ) : items.length === 0 ? (
        <EmptyState
          icon={<Icon name="pill" size={24} />}
          title="No medicines yet"
          description="Upload your discharge paperwork and CareLoop will suggest the medicines listed in it, for you to confirm."
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {items.map((medication) => (
            <MedicationCard
              key={medication.id}
              medication={medication}
              actions={
                <>
                  <AdherenceControl
                    patientId={patientId}
                    patientTimezone={patient?.timezone}
                    medication={medication}
                  />
                  <Button
                    variant="secondary"
                    size="sm"
                    leadingIcon={<Icon name="edit" size={14} />}
                    onClick={() => setModal({ kind: 'edit', medication })}
                  >
                    Edit
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    leadingIcon={<Icon name="bell" size={14} />}
                    onClick={() => setModal({ kind: 'remind', medication })}
                  >
                    Reminder
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    leadingIcon={<Icon name="trash" size={14} />}
                    onClick={() => setModal({ kind: 'delete', medication })}
                    className="text-danger-600 hover:bg-danger-50"
                  >
                    Remove
                  </Button>
                </>
              }
            />
          ))}
        </div>
      )}

      {modal.kind === 'create' ? <CreateMedicationDialog onClose={() => setModal({ kind: 'none' })} /> : null}
      {modal.kind === 'edit' ? <EditMedicationDialog medication={modal.medication} onClose={() => setModal({ kind: 'none' })} /> : null}
      {modal.kind === 'delete' ? <DeleteMedicationDialog medication={modal.medication} onClose={() => setModal({ kind: 'none' })} /> : null}
      {modal.kind === 'remind' ? (
        <ReminderDialog
          medication={modal.medication}
          timezone={patient?.timezone ?? 'UTC'}
          onClose={() => setModal({ kind: 'none' })}
        />
      ) : null}
    </div>
  )
}

/* -------------------------------------------------------------------------- */

function CreateMedicationDialog({ onClose }: { onClose: () => void }) {
  const { patientId } = useActivePatient()
  const create = useCreateMedication()
  const [error, setError] = useState<string | null>(null)

  if (!patientId) return null

  return (
    <Modal
      open
      onClose={onClose}
      title="Add a medicine"
      description="Copy the details from your discharge paperwork exactly."
      busy={create.isPending}
    >
      <MedicationForm
        submitting={create.isPending}
        error={error}
        onCancel={onClose}
        onSubmit={(values: MedicationCreate) => {
          setError(null)
          create.mutate(
            { patientId, body: values },
            { onSuccess: onClose, onError: (cause) => setError(messageFor(cause)) },
          )
        }}
      />
    </Modal>
  )
}

function EditMedicationDialog({ medication, onClose }: { medication: Medication; onClose: () => void }) {
  const { patientId } = useActivePatient()
  const update = useUpdateMedication()
  const [error, setError] = useState<string | null>(null)

  if (!patientId) return null

  return (
    <Modal
      open
      onClose={onClose}
      title="Edit medicine"
      description="Changes are saved to your record. Your discharge paperwork still takes precedence."
      busy={update.isPending}
    >
      <MedicationForm
        initial={medication}
        submitting={update.isPending}
        error={error}
        onCancel={onClose}
        onSubmit={(values: MedicationUpdate) => {
          setError(null)
          update.mutate(
            { patientId, medicationId: medication.id, body: values },
            { onSuccess: onClose, onError: (cause) => setError(messageFor(cause)) },
          )
        }}
      />
    </Modal>
  )
}

function DeleteMedicationDialog({ medication, onClose }: { medication: Medication; onClose: () => void }) {
  const { patientId } = useActivePatient()
  const remove = useDeleteMedication()

  if (!patientId) return null

  return (
    <Modal
      open
      onClose={onClose}
      title="Remove this medicine?"
      description="This removes it from your CareLoop list. It does not change anything on your discharge paperwork."
      size="sm"
      busy={remove.isPending}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={remove.isPending}>
            Keep it
          </Button>
          <Button
            variant="danger"
            loading={remove.isPending}
            onClick={() => remove.mutate({ patientId, medicationId: medication.id }, { onSuccess: onClose })}
          >
            Remove
          </Button>
        </>
      }
    >
      <p className="text-sm text-ink-700">
        <span className="font-medium">{medication.name}</span> · {medication.dosage}
      </p>
      {remove.error ? (
        <Alert tone="danger" className="mt-3">
          {messageFor(remove.error)}
        </Alert>
      ) : null}
    </Modal>
  )
}

function ReminderDialog({
  medication,
  timezone,
  onClose,
}: {
  medication: Medication
  timezone: string
  onClose: () => void
}) {
  const { patientId } = useActivePatient()
  const create = useCreateMedicationReminder()
  const [times, setTimes] = useState<string[]>([''])
  const [recurrence, setRecurrence] = useState<Recurrence>('daily')
  const [error, setError] = useState<string | null>(null)

  if (!patientId) return null

  function addTime() {
    setTimes((current) => [...current, ''])
  }

  function updateTime(index: number, value: string) {
    setTimes((current) => current.map((time, position) => (position === index ? value : time)))
  }

  // An arrow function, not a hoisted declaration: TypeScript keeps the `patientId`
  // narrowing from the guard above inside a closure only when the closure is
  // assigned after the narrowing.
  const onSubmit = async () => {
    const cleaned = times.map((time) => time.trim()).filter(Boolean)
    if (cleaned.length === 0) {
      setError('Add at least one time of day.')
      return
    }
    const invalid = cleaned.find((time) => !isValidLocalTime(time))
    if (invalid) {
      setError(`"${invalid}" is not a valid time. Use 24-hour format like 08:00.`)
      return
    }

    setError(null)
    create.mutate(
      {
        patientId,
        body: {
          medication_id: medication.id,
          times: cleaned,
          timezone,
          recurrence,
          // A one-time reminder has no interval; sending 1 would imply a repeat.
          ...(recurrence === 'none' ? {} : { recurrence_interval: 1 }),
        },
      },
      { onSuccess: onClose },
    )
  }

  return (
    <Modal
      open
      onClose={onClose}
      title={`Remind me about ${medication.name}`}
      description="CareLoop will send a notification at each time, in your timezone."
      busy={create.isPending}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={create.isPending}>
            Cancel
          </Button>
          <Button onClick={() => void onSubmit()} loading={create.isPending}>
            Create reminder
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="space-y-2">
          <p className="text-sm font-medium text-ink-800">Times of day</p>
          {times.map((time, index) => (
            <div key={index} className="flex items-center gap-2">
              <input
                type="time"
                value={time}
                onChange={(event) => updateTime(index, event.target.value)}
                aria-label={`Dose time ${index + 1}`}
                className="h-10 flex-1 rounded-[var(--radius-control)] border border-line-strong bg-surface px-3 text-sm focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-600/25"
              />
              {times.length > 1 ? (
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label={`Remove time ${index + 1}`}
                  onClick={() => setTimes((current) => current.filter((_, position) => position !== index))}
                >
                  <Icon name="close" size={16} />
                </Button>
              ) : null}
            </div>
          ))}
          {times.length < 8 ? (
            <Button variant="subtle" size="sm" leadingIcon={<Icon name="plus" size={14} />} onClick={addTime}>
              Add another time
            </Button>
          ) : null}
        </div>

        <div>
          <label htmlFor="recurrence" className="block text-sm font-medium text-ink-800">
            Repeat
          </label>
          <select
            id="recurrence"
            value={recurrence}
            onChange={(event) => setRecurrence(event.target.value as Recurrence)}
            className="mt-1.5 h-10 w-full rounded-[var(--radius-control)] border border-line-strong bg-surface px-3 text-sm focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-600/25"
          >
            <option value="none">One time</option>
            <option value="daily">Every day</option>
            <option value="weekly">Every week</option>
          </select>
        </div>

        {error ? <Alert tone="danger">{error}</Alert> : null}
        {create.error ? <Alert tone="danger">{messageFor(create.error)}</Alert> : null}
      </div>
    </Modal>
  )
}

function messageFor(cause: unknown): string {
  return cause instanceof ApiError ? cause.userMessage : 'Something went wrong. Please try again.'
}
