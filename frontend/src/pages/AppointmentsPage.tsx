import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Alert } from '@/components/Alert'
import { AppointmentCard } from '@/components/AppointmentCard'
import { Button } from '@/components/Button'
import { Card, CardHeader } from '@/components/Card'
import { EmptyState } from '@/components/EmptyState'
import { ErrorState } from '@/components/ErrorState'
import { Icon } from '@/components/Icon'
import { Input, Textarea } from '@/components/Input'
import { LoadingState } from '@/components/LoadingState'
import { Modal } from '@/components/Modal'
import { Select } from '@/components/Select'
import { PageHeader } from '@/layouts/AppShell'
import { useActivePatient } from '@/features/auth/useActivePatient'
import {
  useAppointments,
  useCreateAppointment,
  useDeleteAppointment,
  useUpdateAppointment,
} from '@/hooks/usePatientData'
import { ApiError } from '@/lib/apiClient'
import { ENUM_LABELS, type Appointment, type AppointmentCreate, type AppointmentStatus } from '@/types/api'

const appointmentSchema = z.object({
  doctor_name: z.string().trim().min(1, 'Enter the doctor or clinic name').max(200),
  date: z.string().min(1, 'Choose the date and time'),
  location: z.string().trim().max(200).optional().or(z.literal('')),
  status: z.string(),
  notes: z.string().trim().max(2000).optional().or(z.literal('')),
})

type AppointmentFormValues = z.input<typeof appointmentSchema>

const STATUS_OPTIONS = (Object.entries(ENUM_LABELS.appointmentStatus) as Array<[AppointmentStatus, string]>).map(
  ([value, label]) => ({ value, label }),
)

export function AppointmentsPage() {
  const { patientId } = useActivePatient()
  const appointments = useAppointments(patientId)
  const [editing, setEditing] = useState<Appointment | null>(null)
  const [creating, setCreating] = useState(false)
  const [deleting, setDeleting] = useState<Appointment | null>(null)

  // Read the clock once per mount, so the upcoming/past split cannot flip
  // between two renders in the same session.
  const [now] = useState(() => Date.now())
  const all = appointments.data ?? []
  const upcoming = all
    .filter((appointment) => new Date(appointment.date).getTime() >= now)
    .sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime())
  const past = all
    .filter((appointment) => new Date(appointment.date).getTime() < now)
    .sort((a, b) => new Date(b.date).getTime() - new Date(a.date).getTime())

  return (
    <div className="px-4 py-6 sm:px-6 lg:px-8">
      <PageHeader
        title="Appointments"
        description="Follow-up visits from your discharge paperwork and anything you have added."
        actions={
          <Button leadingIcon={<Icon name="plus" size={18} />} onClick={() => setCreating(true)}>
            Add appointment
          </Button>
        }
      />

      {appointments.isError ? (
        <ErrorState error={appointments.error} onRetry={() => void appointments.refetch()} />
      ) : appointments.isLoading ? (
        <LoadingState label="Loading appointments" />
      ) : all.length === 0 ? (
        <EmptyState
          icon={<Icon name="calendar" size={24} />}
          title="No appointments yet"
          description="Add a follow-up visit, or upload your discharge paperwork and CareLoop will extract the appointments listed in it."
        />
      ) : (
        <div className="space-y-6">
          <section>
            <h2 className="mb-3 text-[13px] font-semibold uppercase tracking-wide text-ink-400">
              Upcoming
            </h2>
            {upcoming.length === 0 ? (
              <p className="rounded-[var(--radius-card)] border border-dashed border-line-strong px-4 py-6 text-center text-sm text-ink-500">
                Nothing scheduled ahead.
              </p>
            ) : (
              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                {upcoming.map((appointment) => (
                  <AppointmentCard
                    key={appointment.id}
                    appointment={appointment}
                    actions={
                      <>
                        <Button
                          variant="secondary"
                          size="sm"
                          leadingIcon={<Icon name="edit" size={14} />}
                          onClick={() => setEditing(appointment)}
                        >
                          Edit
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          leadingIcon={<Icon name="trash" size={14} />}
                          onClick={() => setDeleting(appointment)}
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
          </section>

          {past.length > 0 ? (
            <section>
              <h2 className="mb-3 text-[13px] font-semibold uppercase tracking-wide text-ink-400">
                Past
              </h2>
              <Card padding="none" className="overflow-hidden">
                <div className="p-4">
                  <CardHeader title={`${past.length} past ${past.length === 1 ? 'appointment' : 'appointments'}`} />
                </div>
                <ul className="divide-y divide-line border-t border-line">
                  {past.map((appointment) => (
                    <li key={appointment.id} className="p-4">
                      <AppointmentCard appointment={appointment} />
                    </li>
                  ))}
                </ul>
              </Card>
            </section>
          ) : null}
        </div>
      )}

      {creating ? <AppointmentDialog title="Add an appointment" onClose={() => setCreating(false)} /> : null}
      {editing ? (
        <AppointmentDialog
          title="Edit appointment"
          initial={editing}
          onClose={() => setEditing(null)}
        />
      ) : null}
      {deleting ? <DeleteDialog appointment={deleting} onClose={() => setDeleting(null)} /> : null}
    </div>
  )
}

function AppointmentDialog({
  title,
  initial,
  onClose,
}: {
  title: string
  initial?: Appointment
  onClose: () => void
}) {
  const { patientId } = useActivePatient()
  const create = useCreateAppointment()
  const update = useUpdateAppointment()
  const [error, setError] = useState<string | null>(null)
  const isEdit = Boolean(initial)

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<AppointmentFormValues>({
    resolver: zodResolver(appointmentSchema),
    defaultValues: {
      doctor_name: initial?.doctor_name ?? '',
      // datetime-local needs a local `YYYY-MM-DDTHH:mm`, not an ISO instant.
      date: initial ? toLocalInputValue(initial.date) : '',
      location: initial?.location ?? '',
      status: initial?.status ?? 'scheduled',
      notes: initial?.notes ?? '',
    },
  })

  if (!patientId) return null

  const pending = create.isPending || update.isPending

  return (
    <Modal open onClose={onClose} title={title} busy={pending}>
      <form
        onSubmit={handleSubmit((values) => {
          setError(null)
          const body: AppointmentCreate = {
            doctor_name: values.doctor_name.trim(),
            // The value is already a local wall-clock time; send it as-is so the
            // backend stores the time the patient actually meant.
            date: values.date,
            location: values.location?.trim() || null,
            status: values.status as AppointmentStatus,
            notes: values.notes?.trim() || null,
          }
          const options = {
            onSuccess: onClose,
            onError: (cause: unknown) => setError(messageFor(cause)),
          }
          if (initial) update.mutate({ patientId, appointmentId: initial.id, body }, options)
          else create.mutate({ patientId, body }, options)
        })}
        className="space-y-4"
        noValidate
      >
        {error ? <Alert tone="danger">{error}</Alert> : null}

        <Input
          label="Doctor or clinic"
          placeholder="e.g. Dr Amara Okafor"
          error={errors.doctor_name?.message}
          {...register('doctor_name')}
          required
        />
        <Input
          type="datetime-local"
          label="Date and time"
          error={errors.date?.message}
          {...register('date')}
          required
        />
        <Input
          label="Location"
          placeholder="e.g. Riverside Clinic, Room 4"
          error={errors.location?.message}
          {...register('location')}
        />
        <Select
          label="Status"
          options={STATUS_OPTIONS}
          error={errors.status?.message}
          {...register('status')}
        />
        <Textarea
          label="Notes"
          placeholder="Anything to bring, or questions to ask"
          rows={3}
          error={errors.notes?.message}
          {...register('notes')}
        />

        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" variant="ghost" onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          <Button type="submit" loading={pending}>
            {isEdit ? 'Save changes' : 'Add appointment'}
          </Button>
        </div>
      </form>
    </Modal>
  )
}

function DeleteDialog({ appointment, onClose }: { appointment: Appointment; onClose: () => void }) {
  const { patientId } = useActivePatient()
  const remove = useDeleteAppointment()
  if (!patientId) return null

  return (
    <Modal
      open
      onClose={onClose}
      title="Remove this appointment?"
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
            onClick={() => remove.mutate({ patientId, appointmentId: appointment.id }, { onSuccess: onClose })}
          >
            Remove
          </Button>
        </>
      }
    >
      <p className="text-sm text-ink-700">
        {appointment.doctor_name} — this only removes it from your CareLoop list.
      </p>
      {remove.error ? (
        <Alert tone="danger" className="mt-3">
          {messageFor(remove.error)}
        </Alert>
      ) : null}
    </Modal>
  )
}

function toLocalInputValue(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function messageFor(cause: unknown): string {
  return cause instanceof ApiError ? cause.userMessage : 'Something went wrong. Please try again.'
}
