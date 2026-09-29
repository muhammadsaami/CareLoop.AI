/**
 * Domain API modules.
 *
 * One function per backend endpoint, named for the action rather than the verb,
 * and returning the exact type from `types/api.ts`. Screen code never builds a
 * URL by hand and never touches `http` directly — that keeps the contract in
 * one auditable place.
 *
 * Endpoint facts worth remembering while reading this file:
 *  - every route below requires `Authorization: Bearer <jwt>` except health
 *  - list endpoints return BARE ARRAYS, except notifications and daily
 *    check-in history which return an envelope
 *  - the backend returns 404 (not 403) for records outside the caller's grants
 */

import { http } from '@/lib/apiClient'
import type {
  AdherenceLog,
  AdherenceLogCreate,
  AdherenceLogUpdate,
  Appointment,
  AppointmentCreate,
  AppointmentReminderCreate,
  AppointmentUpdate,
  CheckIn,
  CheckInCreate,
  CheckInQuestionSet,
  CheckInReminderCreate,
  DailyCheckIn,
  DailyCheckInCreate,
  DailyCheckInHistory,
  DischargeDocument,
  DischargeDocumentDetail,
  Escalation,
  EscalationTransitionRequest,
  ExtractionResult,
  GroundedAnswer,
  GroundedAnswerRequest,
  Health,
  Medication,
  MedicationCreate,
  MedicationReminderCreate,
  MedicationUpdate,
  Notification,
  NotificationList,
  Patient,
  PatientCreate,
  PatientUpdate,
  Readiness,
  Reminder,
  ReminderUpdate,
  WarningSymptom,
  WarningSymptomCreate,
  WarningSymptomUpdate,
} from '@/types/api'

/* --------------------------------- health -------------------------------- */
/** Public. No token required. Used by the sign-in screen to verify reachability. */
export const healthApi = {
  health: () => http.get<Health>('/health', { anonymous: true }),
  readiness: () => http.get<Readiness>('/health/ready', { anonymous: true }),
}

/* -------------------------------- patients ------------------------------- */
export const patientsApi = {
  list: (signal?: AbortSignal) => http.get<Patient[]>('/patients', { signal }),
  create: (body: PatientCreate) => http.post<Patient>('/patients', { json: body }),
  get: (patientId: string, signal?: AbortSignal) =>
    http.get<Patient>(`/patients/${patientId}`, { signal }),
  update: (patientId: string, body: PatientUpdate) =>
    http.patch<Patient>(`/patients/${patientId}`, { json: body }),
}

/* ------------------------------ medications ------------------------------ */
export const medicationsApi = {
  list: (patientId: string, signal?: AbortSignal) =>
    http.get<Medication[]>(`/patients/${patientId}/medications`, { signal }),
  create: (patientId: string, body: MedicationCreate) =>
    http.post<Medication>(`/patients/${patientId}/medications`, { json: body }),
  update: (medicationId: string, body: MedicationUpdate) =>
    http.patch<Medication>(`/medications/${medicationId}`, { json: body }),
  remove: (medicationId: string) => http.delete<void>(`/medications/${medicationId}`),
}

/* ------------------------------ adherence -------------------------------- */
export const adherenceApi = {
  list: (patientId: string, signal?: AbortSignal) =>
    http.get<AdherenceLog[]>(`/patients/${patientId}/adherence`, { signal }),
  record: (patientId: string, body: AdherenceLogCreate) =>
    http.post<AdherenceLog>(`/patients/${patientId}/adherence`, { json: body }),
  update: (adherenceId: string, body: AdherenceLogUpdate) =>
    http.patch<AdherenceLog>(`/adherence/${adherenceId}`, { json: body }),
}

/* ----------------------------- appointments ------------------------------ */
export const appointmentsApi = {
  list: (patientId: string, signal?: AbortSignal) =>
    http.get<Appointment[]>(`/patients/${patientId}/appointments`, { signal }),
  create: (patientId: string, body: AppointmentCreate) =>
    http.post<Appointment>(`/patients/${patientId}/appointments`, { json: body }),
  update: (appointmentId: string, body: AppointmentUpdate) =>
    http.patch<Appointment>(`/appointments/${appointmentId}`, { json: body }),
  remove: (appointmentId: string) => http.delete<void>(`/appointments/${appointmentId}`),
}

/* --------------------------- warning symptoms ---------------------------- */
export const warningSymptomsApi = {
  list: (patientId: string, signal?: AbortSignal) =>
    http.get<WarningSymptom[]>(`/patients/${patientId}/warning-symptoms`, { signal }),
  create: (patientId: string, body: WarningSymptomCreate) =>
    http.post<WarningSymptom>(`/patients/${patientId}/warning-symptoms`, { json: body }),
  update: (symptomId: string, body: WarningSymptomUpdate) =>
    http.patch<WarningSymptom>(`/warning-symptoms/${symptomId}`, { json: body }),
  remove: (symptomId: string) => http.delete<void>(`/warning-symptoms/${symptomId}`),
}

/* --------------------------- discharge documents ------------------------- */
export const documentsApi = {
  list: (patientId: string, signal?: AbortSignal) =>
    http.get<DischargeDocument[]>(`/patients/${patientId}/discharge-documents`, { signal }),
  detail: (documentId: string, signal?: AbortSignal) =>
    http.get<DischargeDocumentDetail>(`/discharge-documents/${documentId}`, { signal }),
  /** multipart/form-data upload; the backend runs OCR + extraction synchronously. */
  upload: (patientId: string, file: File) => {
    const form = new FormData()
    form.append('patient_id', patientId)
    form.append('file', file)
    return http.post<ExtractionResult>('/discharge-documents', { formData: form })
  },
  reprocess: (documentId: string) =>
    http.post<ExtractionResult>(`/discharge-documents/${documentId}/reprocess`),
}

/* ----------------------------- grounded agent ---------------------------- */
export const agentApi = {
  /** Phase 4: answers only from one document, every claim tied to real chunks. */
  ask: (body: GroundedAnswerRequest, signal?: AbortSignal) =>
    http.post<GroundedAnswer>('/agent/query', { json: body, signal }),
}

/* ------------------------------- check-ins ------------------------------- */
export const checkinsApi = {
  /** Server-driven question set. The UI renders these; it never hardcodes them. */
  questions: (patientId: string, signal?: AbortSignal) =>
    http.get<CheckInQuestionSet>(`/patients/${patientId}/checkins/questions`, { signal }),
  history: (patientId: string, query?: { skip?: number; limit?: number }, signal?: AbortSignal) =>
    http.get<DailyCheckInHistory>(`/patients/${patientId}/checkins/daily`, { query, signal }),
  /** Returns null when the patient has never completed a check-in. */
  latest: (patientId: string, signal?: AbortSignal) =>
    http.get<DailyCheckIn | null>(`/patients/${patientId}/checkins/daily/latest`, { signal }),
  get: (patientId: string, checkinId: string, signal?: AbortSignal) =>
    http.get<DailyCheckIn>(`/patients/${patientId}/checkins/daily/${checkinId}`, { signal }),
  submit: (patientId: string, body: DailyCheckInCreate) =>
    http.post<DailyCheckIn>(`/patients/${patientId}/checkins/daily`, { json: body }),
  schedule: (patientId: string, body: CheckInReminderCreate) =>
    http.post<Reminder>(`/patients/${patientId}/checkins/schedule`, { json: body }),
  /** Legacy free-text check-ins. */
  listLegacy: (patientId: string, signal?: AbortSignal) =>
    http.get<CheckIn[]>(`/patients/${patientId}/checkins`, { signal }),
  createLegacy: (patientId: string, body: CheckInCreate) =>
    http.post<CheckIn>(`/patients/${patientId}/checkins`, { json: body }),
  getLegacy: (checkinId: string, signal?: AbortSignal) =>
    http.get<CheckIn>(`/checkins/${checkinId}`, { signal }),
}

/* ------------------------------- escalations ----------------------------- */
export const escalationsApi = {
  list: (patientId: string, signal?: AbortSignal) =>
    http.get<Escalation[]>(`/patients/${patientId}/escalations`, { signal }),
  get: (patientId: string, escalationId: string, signal?: AbortSignal) =>
    http.get<Escalation>(`/patients/${patientId}/escalations/${escalationId}`, { signal }),
  acknowledge: (patientId: string, escalationId: string) =>
    http.post<Escalation>(`/patients/${patientId}/escalations/${escalationId}/acknowledge`),
  resolve: (patientId: string, escalationId: string, body: EscalationTransitionRequest = {}) =>
    http.post<Escalation>(`/patients/${patientId}/escalations/${escalationId}/resolve`, { json: body }),
  cancel: (patientId: string, escalationId: string) =>
    http.post<Escalation>(`/patients/${patientId}/escalations/${escalationId}/cancel`),
}

/* -------------------------------- reminders ------------------------------ */
export const remindersApi = {
  list: (patientId: string, signal?: AbortSignal) =>
    http.get<Reminder[]>(`/patients/${patientId}/reminders`, { signal }),
  update: (patientId: string, reminderId: string, body: ReminderUpdate) =>
    http.patch<Reminder>(`/patients/${patientId}/reminders/${reminderId}`, { json: body }),
  cancel: (patientId: string, reminderId: string) =>
    http.delete<void>(`/patients/${patientId}/reminders/${reminderId}`),
  createForMedication: (patientId: string, body: MedicationReminderCreate) =>
    http.post<Reminder[]>(`/patients/${patientId}/reminders/medication`, { json: body }),
  createForAppointment: (patientId: string, body: AppointmentReminderCreate) =>
    http.post<Reminder>(`/patients/${patientId}/reminders/appointment`, { json: body }),
  notificationHistory: (patientId: string, reminderId: string, signal?: AbortSignal) =>
    http.get<NotificationList>(`/patients/${patientId}/reminders/${reminderId}/notifications`, {
      signal,
    }),
}

/* ------------------------------ notifications ---------------------------- */
export const notificationsApi = {
  /**
   * The backend filters this list by delivery status ONLY (`status`); there is
   * no `notification_type` query parameter. Passing one would be silently
   * ignored by FastAPI and the UI would look like it had filtered when it had
   * not, so the wrapper does not offer it. Type-based views are a backend gap.
   */
  list: (
    patientId: string,
    query?: { skip?: number; limit?: number; status?: string },
    signal?: AbortSignal,
  ) => http.get<NotificationList>(`/patients/${patientId}/notifications`, { query, signal }),
  get: (notificationId: string, signal?: AbortSignal) =>
    http.get<Notification>(`/notifications/${notificationId}`, { signal }),
}
