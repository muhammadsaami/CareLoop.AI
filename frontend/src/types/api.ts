/**
 * CareLoop AI — API contract types.
 *
 * Every type in this file mirrors a Pydantic schema in the backend EXACTLY.
 * Sources of truth:
 *   backend/app/schemas/*.py   and the generated OpenAPI document
 *   (CareLoop AI 3.0.0, 40 paths, 75 schemas)
 *
 * Rules that must hold if this file is ever edited:
 *  1. A field the backend marks optional stays optional here. Treating an
 *     optional field as required is a type error the compiler will not catch
 *     at the boundary where it hurts most.
 *  2. `null` is a real, expected value in this API (caregiver_contact,
 *     discharge_date, timing, notes...). It is modelled, not omitted.
 *  3. Do not rename to prettier names. `patient_id` is `patient_id` on the
 *     wire; renaming it is how a frontend silently breaks an API contract.
 */

/* -------------------------------------------------------------------------
 * Enums — values are the backend's exact string members, in declaration
 * order where order is meaningful (e.g. WellbeingAnswer reads as a scale).
 * ---------------------------------------------------------------------- */

export type AppointmentStatus = 'scheduled' | 'completed' | 'cancelled' | 'missed'
export type CheckInStatus = 'completed' | 'escalated' | 'needs_review'
export type ConditionChange = 'better' | 'same' | 'worse'
export type DeliveryChannel = 'console' | 'whatsapp'
export type DocumentType = 'discharge_summary' | 'medication_list' | 'lab_report' | 'other'
export type EscalationCategory = 'warning_criteria_changed' | 'warning_criteria_present'
export type EscalationStatus = 'pending' | 'notified' | 'acknowledged' | 'resolved' | 'cancelled'
export type EscalationWorkflow = 'review_by_care_team' | 'contact_care_team'
export type ExtractionRunStatus = 'started' | 'completed' | 'failed'
export type ExtractionStatus = 'pending' | 'completed' | 'needs_review' | 'failed'
export type NotificationStatus = 'pending' | 'sending' | 'sent' | 'failed' | 'skipped'
export type NotificationType =
  | 'medication_reminder'
  | 'appointment_reminder'
  | 'checkin_prompt'
  | 'escalation_notice'
export type OcrStatus = 'not_required' | 'completed' | 'failed' | 'skipped'
export type ProcessingStatus = 'pending' | 'processing' | 'completed' | 'failed'
export type Recurrence = 'none' | 'daily' | 'weekly'
export type ReminderStatus = 'active' | 'paused' | 'completed' | 'cancelled'
export type ReminderType = 'medication' | 'appointment' | 'checkin'
export type SymptomChange = 'absent' | 'better' | 'same' | 'worse'
export type SymptomSeverity = 'low' | 'medium' | 'high' | 'critical'
export type WellbeingAnswer = 'good' | 'okay' | 'unwell' | 'very_unwell'

/** Human-readable labels for enum values. Display only, never sent to the API. */
export const ENUM_LABELS = {
  appointmentStatus: {
    scheduled: 'Scheduled',
    completed: 'Completed',
    cancelled: 'Cancelled',
    missed: 'Missed',
  },
  checkInStatus: {
    completed: 'Completed',
    escalated: 'Escalated to care team',
    needs_review: 'Needs review',
  },
  conditionChange: { better: 'Better', same: 'About the same', worse: 'Worse' },
  documentType: {
    discharge_summary: 'Discharge summary',
    medication_list: 'Medication list',
    lab_report: 'Lab report',
    other: 'Other',
  },
  escalationCategory: {
    warning_criteria_changed: 'Warning criteria changed',
    warning_criteria_present: 'Warning criteria present',
  },
  escalationStatus: {
    pending: 'Pending',
    notified: 'Care team notified',
    acknowledged: 'Acknowledged',
    resolved: 'Resolved',
    cancelled: 'Cancelled',
  },
  escalationWorkflow: {
    review_by_care_team: 'For the care team to review',
    contact_care_team: 'Contact the care team',
  },
  extractionStatus: {
    pending: 'Pending',
    completed: 'Completed',
    needs_review: 'Needs review',
    failed: 'Failed',
  },
  notificationStatus: {
    pending: 'Pending',
    sending: 'Sending',
    sent: 'Sent',
    failed: 'Failed',
    skipped: 'Skipped',
  },
  notificationType: {
    medication_reminder: 'Medication reminder',
    appointment_reminder: 'Appointment reminder',
    checkin_prompt: 'Check-in prompt',
    escalation_notice: 'Care team alert',
  },
  processingStatus: {
    pending: 'Pending',
    processing: 'Processing',
    completed: 'Completed',
    failed: 'Failed',
  },
  recurrence: { none: 'One time', daily: 'Every day', weekly: 'Every week' },
  reminderStatus: {
    active: 'Active',
    paused: 'Paused',
    completed: 'Completed',
    cancelled: 'Cancelled',
  },
  reminderType: { medication: 'Medication', appointment: 'Appointment', checkin: 'Daily check-in' },
  symptomChange: {
    absent: 'Not happening now',
    better: 'Better',
    same: 'About the same',
    worse: 'Worse',
  },
  symptomSeverity: { low: 'Low', medium: 'Medium', high: 'High', critical: 'Critical' },
  wellbeing: {
    good: 'Good',
    okay: 'Okay',
    unwell: 'Unwell',
    very_unwell: 'Very unwell',
  },
} as const

/* -------------------------------------------------------------------------
 * Patient
 * ---------------------------------------------------------------------- */

export interface Patient {
  id: string
  name: string
  contact_number: string
  caregiver_contact: string | null
  discharge_date: string | null
  timezone: string
  created_at: string
  updated_at: string
}

export interface PatientCreate {
  name: string
  contact_number: string
  caregiver_contact?: string | null
  discharge_date?: string | null
  timezone?: string
}

export type PatientUpdate = Partial<
  Omit<PatientCreate, 'timezone'> & { timezone?: string }
>

/* -------------------------------------------------------------------------
 * Medication & adherence
 * ---------------------------------------------------------------------- */

export interface Medication {
  id: string
  patient_id: string
  name: string
  dosage: string
  frequency: string
  timing: string | null
  start_date: string | null
  end_date: string | null
  instructions: string | null
  created_at: string
  updated_at: string
}

export interface MedicationCreate {
  name: string
  dosage: string
  frequency: string
  timing?: string | null
  start_date?: string | null
  end_date?: string | null
  instructions?: string | null
}

export type MedicationUpdate = Partial<MedicationCreate>

export interface AdherenceLog {
  id: string
  patient_id: string
  medication_id: string
  scheduled_time: string
  taken: boolean
  taken_time: string | null
  created_at: string
}

export interface AdherenceLogCreate {
  medication_id: string
  scheduled_time: string
  taken: boolean
  taken_time?: string | null
}

export interface AdherenceLogUpdate {
  taken?: boolean
  taken_time?: string | null
}

/* -------------------------------------------------------------------------
 * Appointments
 * ---------------------------------------------------------------------- */

export interface Appointment {
  id: string
  patient_id: string
  doctor_name: string
  date: string
  location: string | null
  status: AppointmentStatus
  notes: string | null
  created_at: string
  updated_at: string
}

export interface AppointmentCreate {
  doctor_name: string
  date: string
  location?: string | null
  status?: AppointmentStatus
  notes?: string | null
}

export type AppointmentUpdate = Partial<AppointmentCreate>

/* -------------------------------------------------------------------------
 * Warning symptoms
 * ---------------------------------------------------------------------- */

export interface WarningSymptom {
  id: string
  patient_id: string
  description: string
  severity: SymptomSeverity
  created_at: string
  updated_at: string
}

export interface WarningSymptomCreate {
  description: string
  severity: SymptomSeverity
}

export type WarningSymptomUpdate = Partial<WarningSymptomCreate>

/* -------------------------------------------------------------------------
 * Discharge documents & extraction
 * ---------------------------------------------------------------------- */

export interface DischargeDocument {
  id: string
  patient_id: string
  original_filename: string
  content_type: string
  file_size: number
  sha256_hash: string
  document_type: DocumentType
  processing_status: ProcessingStatus
  ocr_status: OcrStatus
  extraction_status: ExtractionStatus
  page_count: number | null
  error_message: string | null
  created_at: string
  updated_at: string
}

export interface ExtractionRun {
  id: string
  document_id: string
  provider: string
  model: string
  started_at: string
  completed_at: string | null
  status: ExtractionRunStatus
  raw_ocr_character_count: number | null
  extraction_error: string | null
  validation_error: string | null
}

export interface DischargeDocumentDetail extends DischargeDocument {
  extraction_runs: ExtractionRun[]
}

export interface ExtractionCounts {
  medications: number
  appointments: number
  warning_symptoms: number
  needs_review: number
}

export interface ExtractionResult {
  document_id: string
  patient_id: string
  provider: string
  model: string
  processing_status: string
  ocr_status: string
  extraction_status: string
  page_count: number | null
  counts: ExtractionCounts
  created_appointment_ids: string[]
  created_medication_ids: string[]
  created_warning_symptom_ids: string[]
  message: string | null
}

/* -------------------------------------------------------------------------
 * Grounded agent (Phase 4) & RAG (Phase 3)
 * ---------------------------------------------------------------------- */

export interface GroundedAnswerRequest {
  patient_id: string
  discharge_document_id: string
  query: string
  top_k?: number
}

export interface GroundedSource {
  chunk_id: string
  source_page: number | null
  score: number
}

export interface GroundedAnswer {
  answer: string | null
  supported: boolean
  needs_review: boolean
  safety_flags: string[]
  sources: GroundedSource[]
  trace_id: string
  llm_provider: string | null
}

export interface RetrievedChunk {
  chunk_id: string
  text: string
  source_page: number | null
  score: number
  extraction_run_id: string | null
}

export interface RagRetrieveResult {
  patient_id: string
  document_id: string
  query_length: number
  indexed_chunks: number
  min_score: number
  match_count: number
  chunks: RetrievedChunk[]
  grounded: boolean
  notice: string
}

export interface RagIndexResult {
  document_id: string
  patient_id: string
  chunks_indexed: number
  pages_covered: number
  chunk_size: number
  chunk_overlap: number
  embedding_provider: string
  embedding_dimensions: number
  notice: string
}

/* -------------------------------------------------------------------------
 * Daily check-in (Phase 6)
 * ---------------------------------------------------------------------- */

export interface WarningSymptomOption {
  id: string
  severity: SymptomSeverity
}

export interface CheckInQuestion {
  key: string
  prompt: string
  answer_type: string
  options: string[]
  sourced_from_warning_symptoms: boolean
}

export interface CheckInQuestionSet {
  version: string
  questions: CheckInQuestion[]
  available_warning_symptoms: WarningSymptomOption[]
}

/** One warning-symptom answer. Keys are backend `DailyCheckInCreate` fields. */
export interface SymptomReport {
  symptom_id: string
  change: SymptomChange
}

export interface DailyCheckInCreate {
  date?: string | null
  timezone?: string | null
  general_wellbeing?: WellbeingAnswer | null
  condition_change?: ConditionChange | null
  warning_symptoms?: SymptomReport[]
}

export interface EscalationSummary {
  id: string
  checkin_id: string
  rule_code: string
  rule_version: string
  category: EscalationCategory
  workflow: EscalationWorkflow
  status: EscalationStatus
  severity: string | null
  reason_code: string
  created_at: string
}

export interface DailyCheckIn {
  id: string
  patient_id: string
  date: string
  timezone: string
  status: CheckInStatus
  responses: Record<string, unknown> | null
  needs_review: boolean
  review_reason: string | null
  completed_at: string | null
  escalations: EscalationSummary[]
  patient_message: string
}

export interface DailyCheckInHistoryItem {
  id: string
  date: string
  timezone: string | null
  status: CheckInStatus
  needs_review: boolean
  review_reason: string | null
  responses: Record<string, unknown>
  escalation_count: number
  completed_at: string | null
}

export interface DailyCheckInHistory {
  total: number
  skip: number
  limit: number
  items: DailyCheckInHistoryItem[]
}

export interface CheckInReminderCreate {
  local_time?: string | null
  timezone?: string | null
  notes?: string | null
}

/* -------------------------------------------------------------------------
 * Free-text check-ins (legacy structured endpoint)
 * ---------------------------------------------------------------------- */

export interface CheckIn {
  id: string
  patient_id: string
  date: string
  response_text: string
  flagged: boolean
  flag_reason: string | null
  created_at: string
}

export interface CheckInCreate {
  date: string
  response_text: string
  flagged?: boolean
  flag_reason?: string | null
}

/* -------------------------------------------------------------------------
 * Reminders & notifications
 * ---------------------------------------------------------------------- */

export interface Reminder {
  id: string
  patient_id: string
  reminder_type: ReminderType
  medication_id: string | null
  appointment_id: string | null
  local_time: string | null
  recurrence: Recurrence
  recurrence_interval: number
  frequency_text: string | null
  appointment_at: string | null
  lead_time_minutes: number | null
  timezone: string
  next_occurrence_at: string | null
  active_from: string | null
  active_until: string | null
  status: ReminderStatus
  needs_review: boolean
  review_reason: string | null
  notes: string | null
  created_at: string
  updated_at: string
}

export interface ReminderUpdate {
  status?: ReminderStatus
  start_date?: string | null
  end_date?: string | null
  notes?: string | null
}

export interface MedicationReminderCreate {
  medication_id: string
  /** Explicit local dose times, e.g. ['08:00','20:00'], interpreted in `timezone`. */
  times: string[]
  timezone?: string | null
  recurrence?: Recurrence
  recurrence_interval?: number
  start_date?: string | null
  end_date?: string | null
  notes?: string | null
}

export interface AppointmentReminderCreate {
  appointment_id: string
  lead_time_minutes: number
  timezone?: string | null
  notes?: string | null
}

export interface Notification {
  id: string
  patient_id: string
  reminder_id: string | null
  notification_type: NotificationType
  medication_id: string | null
  appointment_id: string | null
  scheduled_for: string
  timezone: string
  status: NotificationStatus
  channel: DeliveryChannel
  provider_message_id: string | null
  attempt_count: number
  last_error: string | null
  next_retry_at: string | null
  body: string
  recipient: string
  sent_at: string | null
  created_at: string
  updated_at: string
}

export interface NotificationList {
  patient_id: string
  count: number
  skip: number
  limit: number
  notifications: Notification[]
}

/* -------------------------------------------------------------------------
 * Escalations (Phase 6)
 * ---------------------------------------------------------------------- */

export interface Escalation {
  id: string
  patient_id: string
  checkin_id: string
  rule_code: string
  rule_version: string
  category: EscalationCategory
  workflow: EscalationWorkflow
  status: EscalationStatus
  severity: string | null
  warning_symptom_id: string | null
  reason_code: string
  notification_id: string | null
  notification_blocked_reason: string | null
  notified_at: string | null
  acknowledged_at: string | null
  resolved_at: string | null
  resolution_note: string | null
  created_at: string
  updated_at: string
}

export interface EscalationTransitionRequest {
  note?: string | null
}

/* -------------------------------------------------------------------------
 * Health (the only public endpoints)
 * ---------------------------------------------------------------------- */

export interface Health {
  status: string
  service: string
}

export interface Readiness {
  status: string
  service: string
  database: string
  notification_provider: string
  notification_provider_configured: boolean
  scheduler_queue: string
  scheduler_broker_configured: boolean
}

export interface DispatchResult {
  scanned: number
  materialized: number
  skipped_duplicate: number
  skipped_overdue: number
  completed: number
  failed: number
}

/** FastAPI validation error envelope (422). */
export interface ApiValidationError {
  detail: Array<{
    loc: Array<string | number>
    msg: string
    type: string
  }>
}
