import { Link } from 'react-router-dom'
import { Alert } from '@/components/Alert'
import { Card } from '@/components/Card'
import { DocumentCard } from '@/components/DocumentCard'
import { EmptyState } from '@/components/EmptyState'
import { ErrorState } from '@/components/ErrorState'
import { Icon } from '@/components/Icon'
import { LoadingState } from '@/components/LoadingState'
import { PageHeader } from '@/layouts/AppShell'
import { UploadDocumentCard } from '@/features/documents/UploadDocumentCard'
import { WarningSymptomManager } from '@/features/documents/WarningSymptomManager'
import { useActivePatient } from '@/features/auth/useActivePatient'
import { useDocuments } from '@/hooks/usePatientData'

export function DocumentsPage() {
  const { patientId } = useActivePatient()
  const documents = useDocuments(patientId)

  if (!patientId) {
    return (
      <div className="px-4 py-6 sm:px-6 lg:px-8">
        <LoadingState label="Loading patient" />
      </div>
    )
  }

  const items = documents.data ?? []
  const needsReview = items.filter((doc) => doc.extraction_status === 'needs_review')
  const failed = items.filter((doc) => doc.processing_status === 'failed' || doc.extraction_status === 'failed')

  return (
    <div className="px-4 py-6 sm:px-6 lg:px-8">
      <PageHeader
        title="Documents"
        description="Your discharge paperwork. Open a document to review what CareLoop read from it."
      />

      <div className="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="space-y-5">
          <UploadDocumentCard patientId={patientId} />

          {documents.isError ? (
            <ErrorState error={documents.error} onRetry={() => void documents.refetch()} />
          ) : documents.isLoading ? (
            <LoadingState label="Loading documents" />
          ) : items.length === 0 ? (
            <EmptyState
              icon={<Icon name="file" size={24} />}
              title="No documents yet"
              description="Upload your discharge summary or medication list to get started."
            />
          ) : (
            <div className="space-y-3">
              {needsReview.length > 0 ? (
                <Alert tone="warning" title={`${needsReview.length} document${needsReview.length === 1 ? '' : 's'} need review`}>
                  CareLoop read these automatically. Open one to check the details against your
                  paperwork before relying on them.
                </Alert>
              ) : null}

              <div className="grid gap-4 sm:grid-cols-2">
                {items.map((document) => (
                  <DocumentCard
                    key={document.id}
                    document={document}
                    actions={
                      <Link
                        to={`/documents/${document.id}`}
                        className="inline-flex h-8 items-center gap-1.5 rounded-[var(--radius-control)] bg-brand-700 px-3 text-[13px] font-medium text-white transition-colors hover:bg-brand-800"
                      >
                        <Icon name="eye" size={14} />
                        Review
                      </Link>
                    }
                  />
                ))}
              </div>

              {failed.length > 0 ? (
                <Alert tone="danger" title="Some documents could not be read">
                  {failed.length} document{failed.length === 1 ? '' : 's'} failed. You can retry
                  processing from the document's review page.
                </Alert>
              ) : null}
            </div>
          )}
        </div>

        <div className="space-y-5">
          <WarningSymptomManager patientId={patientId} />
          <Card padding="md">
            <h2 className="text-[15px] font-semibold text-ink-900">How reading works</h2>
            <ol className="mt-3 space-y-3 text-[13px] leading-relaxed text-ink-600">
              {[
                'The document is scanned and any text is extracted from the image.',
                'CareLoop suggests medicines, appointments and warning symptoms it finds.',
                'Anything uncertain is flagged for a person to confirm — nothing is treated as final.',
                'You can ask questions about a document and see which page each answer came from.',
              ].map((step, index) => (
                <li key={step} className="flex gap-2.5">
                  <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-brand-50 text-[11px] font-semibold text-brand-700">
                    {index + 1}
                  </span>
                  {step}
                </li>
              ))}
            </ol>
          </Card>
        </div>
      </div>
    </div>
  )
}
