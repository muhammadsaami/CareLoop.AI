import { useEffect, useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Alert } from '@/components/Alert'
import { Button } from '@/components/Button'
import { Card, CardHeader, DetailRow } from '@/components/Card'
import { Icon } from '@/components/Icon'
import { Input } from '@/components/Input'
import { LoadingState } from '@/components/LoadingState'
import { PageHeader } from '@/layouts/AppShell'
import { useActivePatient } from '@/features/auth/useActivePatient'
import { useAuth } from '@/features/auth/useAuth'
import { useUpdatePatient } from '@/hooks/usePatientData'
import { API_BASE_URL } from '@/lib/apiClient'
import { errorMessage } from '@/components/ErrorState'
import { formatDateTime } from '@/lib/format'

const profileSchema = z.object({
  name: z.string().trim().min(1, 'Enter a name').max(200),
  contact_number: z.string().trim().min(1, 'Enter a contact number').max(60),
  caregiver_contact: z.string().trim().max(60).optional().or(z.literal('')),
  discharge_date: z.string().optional().or(z.literal('')),
  timezone: z.string().trim().min(1, 'Enter a timezone').max(64),
})

type ProfileValues = z.input<typeof profileSchema>

/**
 * Settings.
 *
 * Two honest limits are surfaced here rather than hidden:
 *  - there is no preferences/notifications-settings API, so reminder defaults
 *    are not editable, only readable;
 *  - the editable profile fields are exactly `PATCH /patients/{id}`; account
 *    access is managed through your care team and the sign-in screen.
 */
export function SettingsPage() {
  const { patient, patientId, patients, select } = useActivePatient()
  const { signOut } = useAuth()
  const update = useUpdatePatient()
  const [error, setError] = useState<string | null>(null)

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isDirty },
  } = useForm<ProfileValues>({
    resolver: zodResolver(profileSchema),
    defaultValues: {
      name: patient?.name ?? '',
      contact_number: patient?.contact_number ?? '',
      caregiver_contact: patient?.caregiver_contact ?? '',
      discharge_date: patient?.discharge_date ?? '',
      timezone: patient?.timezone ?? 'UTC',
    },
  })

  // Re-seed when the selected patient changes, so the form never shows one
  // patient's details while another is selected. Only the form is reset here —
  // the confirmation is derived from `isDirty` below rather than stored, so
  // editing a field after a save correctly retracts the "saved" banner.
  useEffect(() => {
    if (!patient) return
    reset({
      name: patient.name,
      contact_number: patient.contact_number,
      caregiver_contact: patient.caregiver_contact ?? '',
      discharge_date: patient.discharge_date ?? '',
      timezone: patient.timezone,
    })
  }, [patient, reset])

  const showSaved = update.isSuccess && !update.isError && !isDirty

  if (!patientId || !patient) {
    return (
      <div className="px-4 py-6 sm:px-6 lg:px-8">
        <LoadingState label="Loading your details" />
      </div>
    )
  }

  return (
    <div className="px-4 py-6 sm:px-6 lg:px-8">
      <PageHeader title="Settings" description="Your details, and how this app reaches you." />

      <div className="grid max-w-5xl gap-5 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <div className="space-y-5">
          <Card padding="md">
            <CardHeader
              title="Your details"
              description="This is the record your care team sees."
              icon={<Icon name="users" size={18} />}
            />

            <form
              className="mt-4 space-y-4"
              noValidate
              onSubmit={handleSubmit((values) => {
                setError(null)
                update.mutate(
                  {
                    patientId,
                    name: values.name.trim(),
                    contact_number: values.contact_number.trim(),
                    caregiver_contact: values.caregiver_contact?.trim() || null,
                    discharge_date: values.discharge_date || null,
                    timezone: values.timezone.trim(),
                  },
                  {
                    onSuccess: () => {
                      // Re-baseline the form so `isDirty` goes false and the
                      // confirmation is derived from the mutation, not a flag.
                      reset({
                        name: values.name,
                        contact_number: values.contact_number,
                        caregiver_contact: values.caregiver_contact ?? '',
                        discharge_date: values.discharge_date ?? '',
                        timezone: values.timezone,
                      })
                    },
                  },
                )
              })}
            >
              {error ? <Alert tone="danger">{error}</Alert> : null}
              {showSaved ? <Alert tone="success">Your details have been saved.</Alert> : null}
              {update.error ? (
                <Alert tone="danger" title="Could not save your details">
                  {errorMessage(update.error)}
                </Alert>
              ) : null}

              <Input
                label="Name"
                error={errors.name?.message}
                {...register('name')}
                required
              />
              <Input
                label="Your contact number"
                hint="Used for your own reminders, never for caregiver alerts."
                error={errors.contact_number?.message}
                {...register('contact_number')}
                required
              />
              <Input
                label="Caregiver contact number"
                hint="Leave blank if nobody should be alerted on your behalf."
                error={errors.caregiver_contact?.message}
                {...register('caregiver_contact')}
              />
              <div className="grid gap-4 sm:grid-cols-2">
                <Input
                  type="date"
                  label="Discharge date"
                  error={errors.discharge_date?.message}
                  {...register('discharge_date')}
                />
                <Input
                  label="Timezone"
                  hint="e.g. Europe/London"
                  error={errors.timezone?.message}
                  {...register('timezone')}
                  required
                />
              </div>

              <div className="flex justify-end gap-2 pt-1">
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => {
                    reset()
                    setError(null)
                  }}
                  disabled={update.isPending || !isDirty}
                >
                  Discard
                </Button>
                <Button type="submit" loading={update.isPending} disabled={!isDirty}>
                  Save changes
                </Button>
              </div>
            </form>
          </Card>

          <Card padding="md">
            <CardHeader title="Warning symptoms on your record" icon={<Icon name="alert" size={18} />} />
            <p className="mt-2 text-[13px] leading-relaxed text-ink-500">
              These drive your daily check-in and the alerts sent to your care team. They are managed
              from the Documents page so they stay next to the paperwork they came from.
            </p>
          </Card>
        </div>

        <div className="space-y-5">
          {patients.length > 1 ? (
            <Card padding="md">
              <CardHeader title="Patients you can view" icon={<Icon name="layers" size={18} />} />
              <ul className="mt-3 space-y-1.5">
                {patients.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      onClick={() => select(item.id)}
                      aria-current={item.id === patientId ? 'true' : undefined}
                      className={`flex w-full items-center justify-between rounded-[var(--radius-control)] border px-3 py-2.5 text-left text-sm transition-colors ${
                        item.id === patientId
                          ? 'border-brand-600 bg-brand-50 font-medium text-brand-900'
                          : 'border-line text-ink-700 hover:bg-ink-50'
                      }`}
                    >
                      {item.name}
                      {item.id === patientId ? <Icon name="check" size={15} /> : null}
                    </button>
                  </li>
                ))}
              </ul>
            </Card>
          ) : null}

          <Card padding="md">
            <CardHeader title="Record information" />
            <dl className="mt-2 divide-y divide-line">
              <DetailRow label="Record created" value={formatDateTime(patient.created_at)} />
              <DetailRow label="Last updated" value={formatDateTime(patient.updated_at)} />
              <DetailRow label="Identifier" value={<span className="break-all font-mono text-[11px]">{patient.id}</span>} />
            </dl>
          </Card>

          <Card padding="md">
            <CardHeader title="Session" icon={<Icon name="shield" size={18} />} />
            <p className="mt-2 text-[13px] leading-relaxed text-ink-500">
              Your session lasts for this browser tab only. Signing out clears it from this
              device.
            </p>
            <dl className="mt-2 divide-y divide-line">
              <DetailRow
                label="Service"
                value={<span className="break-all font-mono text-[11px]">{API_BASE_URL || 'same origin'}</span>}
              />
            </dl>
            <Button variant="secondary" fullWidth className="mt-4" leadingIcon={<Icon name="logout" size={18} />} onClick={signOut}>
              Sign out
            </Button>
          </Card>

          <Card padding="md">
            <CardHeader title="Not available yet" />
            <p className="mt-2 text-[13px] leading-relaxed text-ink-500">
              There is no backend endpoint for notification preferences, caregiver management or
              password changes, so those settings are not shown rather than being buttons that do
              nothing.
            </p>
          </Card>
        </div>
      </div>
    </div>
  )
}
