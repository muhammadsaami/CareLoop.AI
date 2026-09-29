import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Alert } from '@/components/Alert'
import { Button } from '@/components/Button'
import { Input, Textarea } from '@/components/Input'
import type { Medication, MedicationCreate } from '@/types/api'

/**
 * Client-side validation mirrors the backend's `MedicationCreate` constraints.
 *
 * This is a UX affordance, not the authority: the backend re-validates every
 * field and its 422 is surfaced verbatim. The schema is kept deliberately close
 * to the Pydantic model so the two cannot disagree in a way that only shows up
 * after submission.
 */
const medicationSchema = z.object({
  name: z.string().trim().min(1, 'Enter the medicine name').max(200, 'That name is too long'),
  dosage: z.string().trim().min(1, 'Enter the dose').max(100, 'That dose is too long'),
  frequency: z.string().trim().min(1, 'Enter how often to take it').max(100),
  timing: z.string().trim().max(200).optional().or(z.literal('')),
  start_date: z.string().optional().or(z.literal('')),
  end_date: z.string().optional().or(z.literal('')),
  instructions: z.string().trim().max(2000).optional().or(z.literal('')),
})

type MedicationFormValues = z.input<typeof medicationSchema>

export interface MedicationFormProps {
  initial?: Medication | null
  onSubmit: (values: MedicationCreate) => void
  onCancel: () => void
  submitting?: boolean
  error?: string | null
}

export function MedicationForm({
  initial,
  onSubmit,
  onCancel,
  submitting = false,
  error,
}: MedicationFormProps) {
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<MedicationFormValues>({
    resolver: zodResolver(medicationSchema),
    defaultValues: {
      name: initial?.name ?? '',
      dosage: initial?.dosage ?? '',
      frequency: initial?.frequency ?? '',
      timing: initial?.timing ?? '',
      start_date: initial?.start_date ?? '',
      end_date: initial?.end_date ?? '',
      instructions: initial?.instructions ?? '',
    },
  })

  return (
    <form
      onSubmit={handleSubmit((values) => {
        // Empty strings must become null, not "", because the backend models
        // "no value" as null and a blank string would be stored as content.
        onSubmit({
          name: values.name.trim(),
          dosage: values.dosage.trim(),
          frequency: values.frequency.trim(),
          timing: values.timing?.trim() || null,
          start_date: values.start_date || null,
          end_date: values.end_date || null,
          instructions: values.instructions?.trim() || null,
        })
      })}
      className="space-y-4"
      noValidate
    >
      {error ? <Alert tone="danger">{error}</Alert> : null}

      <Input
        label="Medicine name"
        placeholder="e.g. Amlodipine"
        error={errors.name?.message}
        {...register('name')}
        required
      />
      <div className="grid gap-4 sm:grid-cols-2">
        <Input
          label="Dose"
          placeholder="e.g. 5 mg"
          error={errors.dosage?.message}
          {...register('dosage')}
          required
        />
        <Input
          label="How often"
          placeholder="e.g. Once daily"
          error={errors.frequency?.message}
          {...register('frequency')}
          required
        />
      </div>
      <Input
        label="When to take it"
        placeholder="e.g. With breakfast"
        hint="Optional. Copy this exactly from your discharge paperwork."
        error={errors.timing?.message}
        {...register('timing')}
      />
      <div className="grid gap-4 sm:grid-cols-2">
        <Input
          type="date"
          label="Start date"
          error={errors.start_date?.message}
          {...register('start_date')}
        />
        <Input
          type="date"
          label="End date"
          error={errors.end_date?.message}
          {...register('end_date')}
        />
      </div>
      <Textarea
        label="Instructions"
        placeholder="Any instructions from your discharge paperwork"
        rows={3}
        error={errors.instructions?.message}
        {...register('instructions')}
      />

      <div className="flex justify-end gap-2 pt-1">
        <Button type="button" variant="ghost" onClick={onCancel} disabled={submitting}>
          Cancel
        </Button>
        <Button type="submit" loading={submitting}>
          {initial ? 'Save changes' : 'Add medicine'}
        </Button>
      </div>
    </form>
  )
}
