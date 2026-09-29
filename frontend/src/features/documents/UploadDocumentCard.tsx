import { useRef, useState } from 'react'
import { Alert } from '@/components/Alert'
import { Button } from '@/components/Button'
import { Card } from '@/components/Card'
import { Icon } from '@/components/Icon'
import { MAX_UPLOAD_BYTES } from '@/lib/apiClient'
import { formatBytes } from '@/lib/format'
import { useUploadDocument } from '@/hooks/usePatientData'
import { ApiError } from '@/lib/apiClient'
import type { ExtractionResult } from '@/types/api'

/**
 * Discharge document upload.
 *
 * Two things this component is careful about:
 *  1. The file size check uses the same limit the backend enforces, so a
 *     hopeless upload is refused in the browser instead of after a long wait.
 *     The displayed size is never rounded in the patient's favour.
 *  2. The upload is synchronous on the backend — OCR and extraction run before
 *     the response returns. So this shows progress honestly: a spinner and the
 *     fact that a long document can take a while, rather than a fake
 *     percentage. Long uploads are not retried automatically, because a retry
 *     can duplicate a document.
 */
export function UploadDocumentCard({ patientId }: { patientId: string }) {
  const inputRef = useRef<HTMLInputElement>(null)
  const upload = useUploadDocument()
  const [file, setFile] = useState<File | null>(null)
  const [clientError, setClientError] = useState<string | null>(null)
  const [result, setResult] = useState<ExtractionResult | null>(null)
  const [dragging, setDragging] = useState(false)

  function choose(next: File | null) {
    setResult(null)
    setClientError(null)
    if (!next) {
      setFile(null)
      return
    }
    if (next.size > MAX_UPLOAD_BYTES) {
      setFile(null)
      setClientError(
        `That file is ${formatBytes(next.size)}. The limit is ${formatBytes(MAX_UPLOAD_BYTES)}.`,
      )
      return
    }
    setFile(next)
  }

  async function submit() {
    if (!file) return
    setClientError(null)
    upload.mutate(
      { patientId, file },
      {
        onSuccess: (value) => {
          setResult(value)
          setFile(null)
          if (inputRef.current) inputRef.current.value = ''
        },
      },
    )
  }

  return (
    <Card padding="md">
      <div className="flex items-start gap-3">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
          <Icon name="upload" size={18} />
        </span>
        <div className="min-w-0">
          <h2 className="text-[15px] font-semibold text-ink-900">Add a discharge document</h2>
          <p className="mt-0.5 text-[13px] text-ink-500">
            A photo or scan of your discharge paperwork, medication list or lab report. CareLoop
            reads it and suggests the details it finds, which you then confirm.
          </p>
        </div>
      </div>

      <div
        onDragOver={(event) => {
          event.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault()
          setDragging(false)
          choose(event.dataTransfer.files[0] ?? null)
        }}
        className={`mt-4 rounded-[var(--radius-card)] border-2 border-dashed px-4 py-6 text-center transition-colors ${
          dragging ? 'border-brand-500 bg-brand-50' : 'border-line-strong bg-surface-muted'
        }`}
      >
        <input
          ref={inputRef}
          type="file"
          accept="image/*,application/pdf"
          className="sr-only"
          onChange={(event) => choose(event.target.files?.[0] ?? null)}
          aria-label="Choose a document to upload"
        />
        <p className="text-sm text-ink-600">
          Drag a file here, or{' '}
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            className="font-medium text-brand-700 underline underline-offset-2"
          >
            choose a file
          </button>
        </p>
        <p className="mt-1 text-[12px] text-ink-400">
          PDF or image, up to {formatBytes(MAX_UPLOAD_BYTES)}
        </p>
      </div>

      {file ? (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-[var(--radius-control)] border border-line bg-surface-muted px-3 py-2.5">
          <span className="min-w-0 text-[13px] text-ink-700">
            <span className="font-medium">{file.name}</span> · {formatBytes(file.size)}
          </span>
          <div className="flex gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setFile(null)
                if (inputRef.current) inputRef.current.value = ''
              }}
              disabled={upload.isPending}
            >
              Clear
            </Button>
            <Button size="sm" onClick={() => void submit()} loading={upload.isPending}>
              Upload
            </Button>
          </div>
        </div>
      ) : null}

      {upload.isPending ? (
        <Alert tone="brand" className="mt-3" title="Reading your document">
          This can take a minute for a long or photographed document. Please keep this page open.
        </Alert>
      ) : null}

      {clientError ? (
        <Alert tone="danger" className="mt-3">
          {clientError}
        </Alert>
      ) : null}

      {upload.error ? (
        <Alert tone="danger" className="mt-3" title="Upload failed">
          {upload.error instanceof ApiError ? upload.error.userMessage : 'The upload did not complete.'}
        </Alert>
      ) : null}

      {result ? (
        <Alert tone={result.extraction_status === 'needs_review' ? 'warning' : 'success'} className="mt-3">
          <p className="font-medium">
            {result.message ?? 'Extraction finished.'}
          </p>
          <p className="mt-1 text-[13px]">
            Found {result.counts.medications} medicine{result.counts.medications === 1 ? '' : 's'},{' '}
            {result.counts.appointments} appointment{result.counts.appointments === 1 ? '' : 's'} and{' '}
            {result.counts.warning_symptoms} warning symptom
            {result.counts.warning_symptoms === 1 ? '' : 's'}.
            {result.counts.needs_review > 0
              ? ` ${result.counts.needs_review} item${result.counts.needs_review === 1 ? '' : 's'} need a person to confirm.`
              : ''}
          </p>
        </Alert>
      ) : null}
    </Card>
  )
}
