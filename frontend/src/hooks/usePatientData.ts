import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  adherenceApi,
  appointmentsApi,
  checkinsApi,
  documentsApi,
  escalationsApi,
  medicationsApi,
  notificationsApi,
  patientsApi,
  remindersApi,
  warningSymptomsApi,
} from '@/api'
import { queryKeys } from '@/lib/queryKeys'
import type {
  AdherenceLog,
  Appointment,
  AppointmentUpdate,
  CheckInQuestionSet,
  DailyCheckIn,
  DailyCheckInHistory,
  DischargeDocument,
  Escalation,
  Medication,
  MedicationUpdate,
  NotificationList,
  Patient,
  Reminder,
  ReminderUpdate,
  WarningSymptom,
} from '@/types/api'

/* --------------------------------------------------------------------------
 * Patients
 * ----------------------------------------------------------------------- */

export function usePatient(patientId: string | null) {
  return useQuery({
    queryKey: patientId ? queryKeys.patients() : ['patients', 'none'],
    queryFn: ({ signal }) => patientsApi.get(patientId!, signal),
    enabled: Boolean(patientId),
  })
}

export function useUpdatePatient() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ patientId, ...body }: { patientId: string } & Partial<Patient>) =>
      patientsApi.update(patientId, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.patients() })
    },
  })
}

/* --------------------------------------------------------------------------
 * Medications & adherence
 * ----------------------------------------------------------------------- */

export function useMedications(patientId: string | null) {
  return useQuery({
    queryKey: queryKeys.medications(patientId ?? ''),
    queryFn: ({ signal }) => medicationsApi.list(patientId!, signal),
    enabled: Boolean(patientId),
  })
}

export function useCreateMedication() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ patientId, body }: { patientId: string; body: Parameters<typeof medicationsApi.create>[1] }) =>
      medicationsApi.create(patientId, body),
    // A new medication can also change what a document review should show, so
    // the document list is refreshed too rather than left stale.
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.medications(variables.patientId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.documents(variables.patientId) })
    },
  })
}

export function useUpdateMedication() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      medicationId,
      body,
    }: {
      patientId: string
      medicationId: string
      body: MedicationUpdate
    }) => medicationsApi.update(medicationId, body),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.medications(variables.patientId) })
    },
  })
}

export function useDeleteMedication() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ medicationId }: { patientId: string; medicationId: string }) =>
      medicationsApi.remove(medicationId),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.medications(variables.patientId) })
    },
  })
}

export function useAdherence(patientId: string | null) {
  return useQuery({
    queryKey: queryKeys.adherence(patientId ?? ''),
    queryFn: ({ signal }) => adherenceApi.list(patientId!, signal),
    enabled: Boolean(patientId),
  })
}

export function useRecordAdherence() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      patientId,
      body,
    }: {
      patientId: string
      body: Parameters<typeof adherenceApi.record>[1]
    }) => adherenceApi.record(patientId, body),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.adherence(variables.patientId) })
    },
  })
}

export function useUpdateAdherence() {
  const queryClient = useQueryClient()
  return useMutation({
    // The request itself is `/adherence/{id}`; `patientId` is carried through
    // the mutation variables purely so onSuccess can invalidate that patient's
    // adherence cache.
    mutationFn: ({
      patientId: _patientId,
      adherenceId,
      body,
    }: {
      patientId: string
      adherenceId: string
      body: Parameters<typeof adherenceApi.update>[1]
    }) => adherenceApi.update(adherenceId, body),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.adherence(variables.patientId) })
    },
  })
}

/* --------------------------------------------------------------------------
 * Appointments
 * ----------------------------------------------------------------------- */

export function useAppointments(patientId: string | null) {
  return useQuery({
    queryKey: queryKeys.appointments(patientId ?? ''),
    queryFn: ({ signal }) => appointmentsApi.list(patientId!, signal),
    enabled: Boolean(patientId),
  })
}

export function useCreateAppointment() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      patientId,
      body,
    }: { patientId: string; body: Parameters<typeof appointmentsApi.create>[1] }) =>
      appointmentsApi.create(patientId, body),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.appointments(variables.patientId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.documents(variables.patientId) })
    },
  })
}

export function useUpdateAppointment() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      appointmentId,
      body,
    }: {
      patientId: string
      appointmentId: string
      body: AppointmentUpdate
    }) => appointmentsApi.update(appointmentId, body),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.appointments(variables.patientId) })
    },
  })
}

export function useDeleteAppointment() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ appointmentId }: { patientId: string; appointmentId: string }) =>
      appointmentsApi.remove(appointmentId),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.appointments(variables.patientId) })
    },
  })
}

/* --------------------------------------------------------------------------
 * Warning symptoms
 * ----------------------------------------------------------------------- */

export function useWarningSymptoms(patientId: string | null) {
  return useQuery({
    queryKey: queryKeys.warningSymptoms(patientId ?? ''),
    queryFn: ({ signal }) => warningSymptomsApi.list(patientId!, signal),
    enabled: Boolean(patientId),
  })
}

export function useCreateWarningSymptom() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      patientId,
      body,
    }: { patientId: string; body: Parameters<typeof warningSymptomsApi.create>[1] }) =>
      warningSymptomsApi.create(patientId, body),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.warningSymptoms(variables.patientId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.checkinQuestions(variables.patientId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.documents(variables.patientId) })
    },
  })
}

export function useUpdateWarningSymptom() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      symptomId,
      body,
    }: {
      patientId: string
      symptomId: string
      body: Parameters<typeof warningSymptomsApi.update>[1]
    }) => warningSymptomsApi.update(symptomId, body),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.warningSymptoms(variables.patientId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.checkinQuestions(variables.patientId) })
    },
  })
}

export function useDeleteWarningSymptom() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ symptomId }: { patientId: string; symptomId: string }) =>
      warningSymptomsApi.remove(symptomId),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.warningSymptoms(variables.patientId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.checkinQuestions(variables.patientId) })
    },
  })
}

/* --------------------------------------------------------------------------
 * Documents
 * ----------------------------------------------------------------------- */

export function useDocuments(patientId: string | null) {
  return useQuery({
    queryKey: queryKeys.documents(patientId ?? ''),
    queryFn: ({ signal }) => documentsApi.list(patientId!, signal),
    enabled: Boolean(patientId),
  })
}

export function useDocument(documentId: string | null) {
  return useQuery({
    queryKey: queryKeys.document(documentId ?? ''),
    queryFn: ({ signal }) => documentsApi.detail(documentId!, signal),
    enabled: Boolean(documentId),
  })
}

export function useUploadDocument() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ patientId, file }: { patientId: string; file: File }) =>
      documentsApi.upload(patientId, file),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.documents(variables.patientId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.medications(variables.patientId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.appointments(variables.patientId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.warningSymptoms(variables.patientId) })
    },
  })
}

export function useReprocessDocument() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ documentId }: { patientId: string; documentId: string }) =>
      documentsApi.reprocess(documentId),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.document(variables.documentId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.documents(variables.patientId) })
    },
  })
}

/* --------------------------------------------------------------------------
 * Check-ins
 * ----------------------------------------------------------------------- */

export function useCheckinQuestions(patientId: string | null) {
  return useQuery({
    queryKey: queryKeys.checkinQuestions(patientId ?? ''),
    queryFn: ({ signal }) => checkinsApi.questions(patientId!, signal),
    enabled: Boolean(patientId),
    // The question set is versioned server-side; a long cache would let a
    // patient answer against a retired question set.
    staleTime: 60_000,
  })
}

export function useLatestCheckin(patientId: string | null) {
  return useQuery({
    queryKey: queryKeys.checkinLatest(patientId ?? ''),
    queryFn: ({ signal }) => checkinsApi.latest(patientId!, signal),
    enabled: Boolean(patientId),
  })
}

export function useCheckinHistory(patientId: string | null) {
  return useQuery({
    queryKey: queryKeys.checkinHistory(patientId ?? ''),
    queryFn: ({ signal }) => checkinsApi.history(patientId!, { limit: 30 }, signal),
    enabled: Boolean(patientId),
  })
}

export function useSubmitCheckin() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      patientId,
      body,
    }: { patientId: string; body: Parameters<typeof checkinsApi.submit>[1] }) =>
      checkinsApi.submit(patientId, body),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.checkinLatest(variables.patientId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.checkinHistory(variables.patientId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.escalations(variables.patientId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.notifications(variables.patientId) })
    },
  })
}

export function useScheduleCheckin() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      patientId,
      body,
    }: { patientId: string; body: Parameters<typeof checkinsApi.schedule>[1] }) =>
      checkinsApi.schedule(patientId, body),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.reminders(variables.patientId) })
    },
  })
}

/* --------------------------------------------------------------------------
 * Escalations
 * ----------------------------------------------------------------------- */

export function useEscalations(patientId: string | null) {
  return useQuery({
    queryKey: queryKeys.escalations(patientId ?? ''),
    queryFn: ({ signal }) => escalationsApi.list(patientId!, signal),
    enabled: Boolean(patientId),
  })
}

export function useEscalationAction(patientId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      escalationId,
      action,
      note,
    }: {
      escalationId: string
      action: 'acknowledge' | 'resolve' | 'cancel'
      note?: string
    }) => {
      if (action === 'acknowledge') return escalationsApi.acknowledge(patientId, escalationId)
      if (action === 'cancel') return escalationsApi.cancel(patientId, escalationId)
      return escalationsApi.resolve(patientId, escalationId, { note: note ?? null })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.escalations(patientId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.notifications(patientId) })
    },
  })
}

/* --------------------------------------------------------------------------
 * Reminders & notifications
 * ----------------------------------------------------------------------- */

export function useReminders(patientId: string | null) {
  return useQuery({
    queryKey: queryKeys.reminders(patientId ?? ''),
    queryFn: ({ signal }) => remindersApi.list(patientId!, signal),
    enabled: Boolean(patientId),
  })
}

export function useUpdateReminder() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      patientId,
      reminderId,
      body,
    }: {
      patientId: string
      reminderId: string
      body: ReminderUpdate
    }) => remindersApi.update(patientId, reminderId, body),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.reminders(variables.patientId) })
    },
  })
}

export function useCreateMedicationReminder() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      patientId,
      body,
    }: { patientId: string; body: Parameters<typeof remindersApi.createForMedication>[1] }) =>
      remindersApi.createForMedication(patientId, body),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.reminders(variables.patientId) })
    },
  })
}

export function useNotifications(patientId: string | null, filters?: { status?: string }) {
  return useQuery({
    queryKey: queryKeys.notifications(patientId ?? '', filters),
    queryFn: ({ signal }) => notificationsApi.list(patientId!, filters, signal),
    enabled: Boolean(patientId),
  })
}

/* --------------------------------------------------------------------------
 * Type re-exports so screens import from one place.
 * ----------------------------------------------------------------------- */

export type {
  AdherenceLog,
  Appointment,
  CheckInQuestionSet,
  DailyCheckIn,
  DailyCheckInHistory,
  DischargeDocument,
  Escalation,
  Medication,
  NotificationList,
  Reminder,
  WarningSymptom,
}
