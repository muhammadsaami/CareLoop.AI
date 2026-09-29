import { Badge, type BadgeTone } from './Badge'
import { cn } from '@/lib/cn'
import { formatBytes, formatDateTime } from '@/lib/format'
import { ENUM_LABELS, type DischargeDocument, type DocumentType, type ExtractionStatus, type ProcessingStatus } from '@/types/api'
import type { ReactNode } from 'react'

const TYPE_LABEL: Record<DocumentType, string> = {
  discharge_summary: 'Discharge summary',
  medication_list: 'Medication list',
  lab_report: 'Lab report',
  other: 'Other',
}

const EXTRACTION_TONE: Record<ExtractionStatus, BadgeTone> = {
  pending: 'neutral',
  completed: 'success',
  needs_review: 'warning',
  failed: 'danger',
}

const PROCESSING_TONE: Record<ProcessingStatus, BadgeTone> = {
  pending: 'neutral',
  processing: 'info',
  completed: 'success',
  failed: 'danger',
}

/** A document that failed processing is a real failure, so it reads as one. */
// The failure predicate sits beside the card that uses it, so the rule is defined once.
// oxlint-disable-next-line react/only-export-components
export function isFailed(document: DischargeDocument): boolean {
  return document.processing_status === 'failed' || document.extraction_status === 'failed'
}

export interface DocumentCardProps {
  document: DischargeDocument
  actions?: ReactNode
  onClick?: () => void
  className?: string
}

const CARD_BASE =
  'w-full rounded-[var(--radius-card)] border border-line bg-surface p-4 text-left shadow-soft'

export function DocumentCard({ document, actions, onClick, className }: DocumentCardProps) {
  const failed = isFailed(document)
  const inProgress = document.processing_status === 'processing'

  const body = (
    <>
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <span
            className={cn(
              'flex size-10 shrink-0 items-center justify-center rounded-lg',
              failed ? 'bg-danger-50 text-danger-600' : 'bg-brand-50 text-brand-600',
            )}
          >
            <svg
              className="size-5"
              viewBox="0 0 20 20"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              aria-hidden="true"
            >
              <path d="M11.5 2.5H6a1.5 1.5 0 0 0-1.5 1.5v12A1.5 1.5 0 0 0 6 17.5h8a1.5 1.5 0 0 0 1.5-1.5V6.5l-4-4Z" />
              <path d="M11.5 2.5v4h4" />
            </svg>
          </span>
          <div className="min-w-0">
            <p className="truncate text-[15px] font-semibold text-ink-900">{document.original_filename}</p>
            <p className="mt-0.5 text-[13px] text-ink-500">
              {TYPE_LABEL[document.document_type] ?? ENUM_LABELS.documentType[document.document_type]}
              {document.page_count ? ` · ${document.page_count} pages` : ''} ·{' '}
              {formatBytes(document.file_size)}
            </p>
          </div>
        </div>
        <Badge tone={PROCESSING_TONE[document.processing_status] ?? 'neutral'} dot>
          {ENUM_LABELS.processingStatus[document.processing_status] ?? document.processing_status}
        </Badge>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Badge
          tone={EXTRACTION_TONE[document.extraction_status] ?? 'neutral'}
          size="sm"
        >
          Extraction: {ENUM_LABELS.extractionStatus[document.extraction_status] ?? document.extraction_status}
        </Badge>
        <span className="text-[12px] text-ink-400">Added {formatDateTime(document.created_at)}</span>
      </div>

      {inProgress ? (
        <div className="mt-3">
          <div
            className="h-1.5 overflow-hidden rounded-full bg-ink-100"
            role="progressbar"
            aria-label="Document processing"
            aria-valuetext="In progress"
          >
            <div className="h-full w-1/3 animate-pulse rounded-full bg-brand-500" />
          </div>
          <p className="mt-1.5 text-[12px] text-ink-500">
            Reading the document. This can take a minute for long documents.
          </p>
        </div>
      ) : null}

      {document.extraction_status === 'needs_review' ? (
        <p className="mt-3 rounded-[var(--radius-control)] border border-warning-200 bg-warning-50 px-3 py-2 text-[13px] font-medium text-warning-700">
          Some extracted details need a person to confirm them.
        </p>
      ) : null}

      {document.error_message ? (
        <p className="mt-3 rounded-[var(--radius-control)] border border-danger-200 bg-danger-50 px-3 py-2 text-[13px] font-medium text-danger-700">
          {document.error_message}
        </p>
      ) : null}
    </>
  )

  return (
    <div className={cn('space-y-3', className)}>
      {onClick ? (
        <button
          type="button"
          onClick={onClick}
          className={cn(CARD_BASE, 'transition-shadow duration-150 hover:shadow-raised')}
        >
          {body}
        </button>
      ) : (
        <div className={CARD_BASE}>{body}</div>
      )}
      {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
    </div>
  )
}
