/**
 * Centralised TanStack Query keys.
 *
 * Every key is built from the patient id, so switching patients can never show
 * the previous patient's cached clinical data. There is deliberately no global
 * `invalidateQueries()`: mutations invalidate exactly the keys they affect.
 */
export const queryKeys = {
  patients: () => ['patients'] as const,

  medications: (patientId: string) => ['medications', patientId] as const,
  adherence: (patientId: string) => ['adherence', patientId] as const,

  appointments: (patientId: string) => ['appointments', patientId] as const,

  warningSymptoms: (patientId: string) => ['warning-symptoms', patientId] as const,

  documents: (patientId: string) => ['documents', patientId] as const,
  document: (documentId: string) => ['document', documentId] as const,

  checkinQuestions: (patientId: string) => ['checkin-questions', patientId] as const,
  checkinLatest: (patientId: string) => ['checkin-latest', patientId] as const,
  checkinHistory: (patientId: string) => ['checkin-history', patientId] as const,

  escalations: (patientId: string) => ['escalations', patientId] as const,

  reminders: (patientId: string) => ['reminders', patientId] as const,

  notifications: (patientId: string, filters?: { status?: string; type?: string }) =>
    ['notifications', patientId, filters ?? {}] as const,
  notification: (notificationId: string) => ['notification', notificationId] as const,
} as const
