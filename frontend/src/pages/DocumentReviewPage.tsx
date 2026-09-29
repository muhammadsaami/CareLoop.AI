import { useMemo } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Alert } from '@/components/Alert'
import { Badge } from '@/components/Badge'
import { Button } from '@/components/Button'
import { Card, CardHeader, DetailRow } from '@/components/Card'
import { ErrorState } from '@/components/ErrorState'
import { Icon } from '@/components/Icon'
import { LoadingState } from '@/components/LoadingState'
import { MedicationRow } from '@/components/MedicationCard'
import { ReviewBanner } from '@/components/ReviewBanner'
import { useActivePatient } from '@/features/auth/useActivePatient'
import { useAppointments, useDocument, useMedications, useReprocessDocument, useWarningSymptoms } from '@/hooks/usePatientData'
import { formatBytes, formatDateTime, humanizeEnum } from '@/lib/format'
import { errorMessage } from '@/components/ErrorState'
import { ENUM_LABELS } from '@/types/api'

/**
 * Extraction review.
 *
 * This is the screen that exists because extraction is automated and therefore
 * fallible. It shows what the pipeline produced, states plainly what has and has
 * not been confirmed, and gives the patient a way to fix a wrong value without
 * asking them to re-upload anything.
 *
 * The count the backend reports as `needs_review` is never hidden behind a
 * neutral "done" state; if extraction is uncertain, the page says so at the top
 * before showing anything else.
 */
export function DocumentReviewPage() {
  const { documentId } = useParams<{ documentId: string }>()
  const { patientId } = useActivePatient()
  const navigate = useNavigate()

  const document = useDocument(documentId ?? null)
  const reprocess = useReprocessDocument()

  // The extracted records are patient-scoped, not document-scoped, so the review
  // lists what currently exists and lets the patient confirm each one.
  const medications = useMedications(patientId)
  const appointments = useAppointments(patientId)
  const symptoms = useWarningSymptoms(patientId)

  const isLoading = document.isLoading || medications.isLoading || appointments.isLoading

  const recordCount = useMemo(
    () => (medications.data?.length ?? 0) + (appointments.data?.length ?? 0) + (symptoms.data?.length ?? 0),
    [medications.data, appointments.data, symptoms.data],
  )

  if (isLoading) {
    return (
      <div className="px-4 py-6 sm:px-6 lg:px-8">
        <LoadingState label="Loading document" />
      </div>
    )
  }

  if (document.isError) {
    return (
      <div className="px-4 py-6 sm:px-6 lg:px-8">
        <ErrorState error={document.error} onRetry={() => void document.refetch()} />
      </div>
    )
  }

  const doc = document.data
  if (!doc) {
    return (
      <div className="px-4 py-6 sm:px-6 lg:px-8">
        <ErrorState error={new Error('This document is no longer available.')} />
      </div>
    )
  }

  return (
    <div className="px-4 py-6 sm:px-6 lg:px-8">
      <button
        type="button"
        onClick={() => navigate('/documents')}
        className="mb-4 inline-flex items-center gap-1.5 text-[13px] font-medium text-ink-500 transition-colors hover:text-brand-700"
      >
        <Icon name="arrowLeft" size={15} />
        All documents
      </button>

      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight text-ink-950">{doc.original_filename}</h1>
          <p className="mt-1 text-sm text-ink-500">
            {ENUM_LABELS.documentType[doc.document_type] ?? doc.document_type} ·{' '}
            {doc.page_count ? `${doc.page_count} pages · ` : ''}
            {formatBytes(doc.file_size)} · added {formatDateTime(doc.created_at)}
          </p>
        </div>
        <div className="flex shrink-0 gap-2">
          <Button
            variant="secondary"
            leadingIcon={<Icon name="sparkle" size={18} />}
            onClick={() => navigate(`/assistant?document=${doc.id}`)}
          >
            Ask about this
          </Button>
          {patientId ? (
            <Button
              variant="ghost"
              leadingIcon={<Icon name="refresh" size={18} />}
              loading={reprocess.isPending}
              onClick={() =>
                reprocess.mutate({ patientId, documentId: doc.id }, { onSuccess: () => void document.refetch() })
              }
            >
              Re-read
            </Button>
          ) : null}
        </div>
      </div>

      {/* Status and uncertainty, stated before any extracted content. */}
      <div className="mb-5 space-y-3">
        {doc.extraction_status === 'needs_review' ? (
          <ReviewBanner
            reason="CareLoop could not read part of this document confidently. Everything below is a suggestion — check it against the original before relying on it."
          />
        ) : null}

        {doc.processing_status === 'failed' || doc.extraction_status === 'failed' ? (
          <Alert tone="danger" title="This document could not be processed">
            {doc.error_message ??
              'CareLoop could not read this document. Try re-reading it, or upload a clearer photo or scan.'}
            {reprocess.error ? (
              <p className="mt-1 text-[13px]">{errorMessage(reprocess.error)}</p>
            ) : null}
          </Alert>
        ) : null}

        {doc.processing_status === 'processing' ? (
          <Alert tone="brand" title="Still being read">
            This document is being processed. Reload the page in a moment.
          </Alert>
        ) : null}

        {doc.extraction_status === 'completed' ? (
          <Alert tone="success" title="Read successfully">
            The details below were extracted from this document. Confirm anything that looks
            different from your paperwork.
          </Alert>
        ) : null}
      </div>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="space-y-5">
          <ExtractedSection
            title="Medicines found"
            icon="pill"
            isLoading={medications.isLoading}
            emptyText="No medicines are recorded for this patient yet."
            count={medications.data?.length ?? 0}
            editTo="/medications"
          >
            <ul className="divide-y divide-line">
              {(medications.data ?? []).map((medication) => (
                <MedicationRow key={medication.id} medication={medication} />
              ))}
            </ul>
          </ExtractedSection>

          <ExtractedSection
            title="Appointments found"
            icon="calendar"
            isLoading={appointments.isLoading}
            emptyText="No appointments are recorded for this patient yet."
            count={appointments.data?.length ?? 0}
            editTo="/appointments"
          >
            <ul className="divide-y divide-line">
              {(appointments.data ?? []).map((appointment) => (
                <li key={appointment.id} className="py-2.5">
                  <p className="text-sm font-medium text-ink-800">{appointment.doctor_name}</p>
                  <p className="text-[13px] text-ink-500">{formatDateTime(appointment.date)}</p>
                </li>
              ))}
            </ul>
          </ExtractedSection>

          <ExtractedSection
            title="Warning symptoms found"
            icon="alert"
            isLoading={symptoms.isLoading}
            emptyText="No warning symptoms are recorded for this patient yet."
            count={symptoms.data?.length ?? 0}
            editTo="/documents"
          >
            <ul className="divide-y divide-line">
              {(symptoms.data ?? []).map((symptom) => (
                <li key={symptom.id} className="flex items-center justify-between gap-3 py-2.5">
                  <p className="text-sm text-ink-800">{symptom.description}</p>
                  <Badge tone={symptom.severity === 'critical' || symptom.severity === 'high' ? 'danger' : 'warning'} size="sm">
                    {ENUM_LABELS.symptomSeverity[symptom.severity] ?? symptom.severity}
                  </Badge>
                </li>
              ))}
            </ul>
          </ExtractedSection>
        </div>

        <Card padding="md" className="h-fit">
          <CardHeader title="Document details" />
          <dl className="mt-2 divide-y divide-line">
            <DetailRow label="Status" value={humanizeEnum(doc.processing_status, ENUM_LABELS.processingStatus)} />
            <DetailRow label="Text extraction" value={humanizeEnum(doc.ocr_status)} />
            <DetailRow label="Detail extraction" value={humanizeEnum(doc.extraction_status, ENUM_LABELS.extractionStatus)} />
            <DetailRow label="Pages" value={doc.page_count ?? 'Not recorded'} />
            <DetailRow label="File type" value={doc.content_type} />
            <DetailRow
              label="Fingerprint"
              value={<span className="break-all font-mono text-[11px] text-ink-400">{doc.sha256_hash}</span>}
            />
          </dl>

          {doc.extraction_runs.length > 0 ? (
            <>
              <h3 className="mt-4 text-[13px] font-semibold text-ink-800">Reading attempts</h3>
              <ul className="mt-2 space-y-2">
                {doc.extraction_runs.map((run) => (
                  <li
                    key={run.id}
                    className="rounded-[var(--radius-control)] border border-line bg-surface-muted px-3 py-2"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[12px] text-ink-600">{formatDateTime(run.started_at)}</span>
                      <Badge
                        tone={run.status === 'completed' ? 'success' : run.status === 'failed' ? 'danger' : 'neutral'}
                        size="sm"
                      >
                        {humanizeEnum(run.status)}
                      </Badge>
                    </div>
                    {run.extraction_error ? (
                      <p className="mt-1 text-[12px] text-danger-600">{run.extraction_error}</p>
                    ) : null}
                    {run.validation_error ? (
                      <p className="mt-1 text-[12px] text-warning-700">{run.validation_error}</p>
                    ) : null}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </Card>
      </div>

      {recordCount === 0 && !isLoading ? (
        <Alert tone="info" className="mt-5">
          This document has not produced any records yet.{' '}
          <Link to="/documents" className="font-medium text-brand-700 underline underline-offset-2">
            Return to documents
          </Link>
        </Alert>
      ) : null}
    </div>
  )
}

function ExtractedSection({
  title,
  icon,
  isLoading,
  emptyText,
  count,
  editTo,
  children,
}: {
  title: string
  icon: 'pill' | 'calendar' | 'alert'
  isLoading: boolean
  emptyText: string
  count: number
  editTo: string
  children: React.ReactNode
}) {
  return (
    <Card padding="md">
      <CardHeader
        title={title}
        icon={<Icon name={icon} size={18} />}
        action={
          <Link
            to={editTo}
            className="inline-flex h-8 items-center gap-1.5 rounded-[var(--radius-control)] px-3 text-[13px] font-medium text-ink-600 transition-colors hover:bg-ink-100 hover:text-ink-900"
          >
            <Icon name="edit" size={14} />
            Manage
          </Link>
        }
      />
      <div className="mt-2">
        {isLoading ? <LoadingState label={`Loading ${title.toLowerCase()}`} /> : count === 0 ? (
          <p className="py-3 text-[13px] text-ink-500">{emptyText}</p>
        ) : (
          children
        )}
      </div>
    </Card>
  )
}
