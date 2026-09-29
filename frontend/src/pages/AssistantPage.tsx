import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import { agentApi } from '@/api'
import { AIMessage } from '@/components/AIMessage'
import { Alert } from '@/components/Alert'
import { Button } from '@/components/Button'
import { Card, CardHeader } from '@/components/Card'
import { EmptyState } from '@/components/EmptyState'
import { errorMessage } from '@/components/ErrorState'
import { Icon } from '@/components/Icon'
import { LoadingState } from '@/components/LoadingState'
import { Select } from '@/components/Select'
import { SourceList } from '@/components/SourceCitation'
import { Textarea } from '@/components/Input'
import { PageHeader } from '@/layouts/AppShell'
import { useActivePatient } from '@/features/auth/useActivePatient'
import { useDocuments } from '@/hooks/usePatientData'
import type { GroundedAnswer } from '@/types/api'

/**
 * The grounded CareLoop assistant.
 *
 * The API is `POST /agent/query` and it is deliberately constrained: one
 * patient, ONE document, and every sentence traced to a retrieved chunk. So
 * this UI has no conversation, no history, no follow-ups and no memory — those
 * are all things a medical summary must not imply. You pick a document, you ask
 * one question, you get an answer with its sources, or you get an honest "no
 * answer was given".
 */
export function AssistantPage() {
  const { patientId } = useActivePatient()
  const [searchParams, setSearchParams] = useSearchParams()
  const documents = useDocuments(patientId)

  // Failed extractions are excluded: asking a question about a document the
  // backend could not read would return nothing and look like a broken feature.
  const available = useMemo(
    () => (documents.data ?? []).filter((document) => document.extraction_status !== 'failed'),
    [documents.data],
  )
  const preselected = searchParams.get('document')

  const [chosen, setChosen] = useState('')
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState<GroundedAnswer | null>(null)
  const [askError, setAskError] = useState<string | null>(null)

  // Derived, not synchronised: a chosen document wins when it is still in the
  // list, then a valid ?document= from the URL, then the first readable one.
  // Deriving means there is never a render where the select and the request
  // disagree, and a document that disappears cannot stay selected.
  const documentId = available.some((document) => document.id === chosen)
    ? chosen
    : available.some((document) => document.id === preselected)
      ? (preselected as string)
      : (available[0]?.id ?? '')

  const ask = useMutation({
    mutationFn: () =>
      agentApi.ask({
        patient_id: patientId!,
        discharge_document_id: documentId,
        query: question.trim(),
      }),
    onSuccess: (result) => {
      setAnswer(result)
      setAskError(null)
    },
    onError: (cause) => {
      setAnswer(null)
      setAskError(errorMessage(cause, 'CareLoop could not answer right now. Please try again.'))
    },
  })

  const selectedDocument = available.find((document) => document.id === documentId)

  return (
    <div className="px-4 py-6 sm:px-6 lg:px-8">
      <PageHeader
        title="CareLoop assistant"
        description="Ask a question about one of your documents. Every answer is taken from that document only, and shows which page it came from."
      />

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)]">
        <Card padding="md" className="h-fit">
          <CardHeader title="Ask a question" icon={<Icon name="sparkle" size={18} />} />

          {documents.isLoading ? (
            <div className="mt-4">
              <LoadingState label="Loading documents" />
            </div>
          ) : available.length === 0 ? (
            <div className="mt-4">
              <EmptyState
                size="sm"
                title="No readable documents"
                description="Upload a discharge document first, then come back and ask about it."
              />
            </div>
          ) : (
            <form
              className="mt-4 space-y-4"
              noValidate
              onSubmit={(event) => {
                event.preventDefault()
                if (!patientId) return
                if (!question.trim()) {
                  setAskError('Type a question first.')
                  return
                }
                if (!documentId) {
                  setAskError('Choose a document to ask about.')
                  return
                }
                ask.mutate()
              }}
            >
              <Select
                label="About which document?"
                value={documentId}
                onChange={(event) => {
                  setChosen(event.target.value)
                  setSearchParams(event.target.value ? { document: event.target.value } : {}, { replace: true })
                }}
                options={available.map((document) => ({
                  value: document.id,
                  label: document.original_filename,
                }))}
                placeholder="Choose a document"
              />

              <Textarea
                label="Your question"
                placeholder="e.g. What does my paperwork say about how long to take the antibiotics?"
                rows={4}
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                maxLength={2000}
                hint="Ask about what the document says. CareLoop will not give advice about what to do."
              />

              {askError ? <Alert tone="danger">{askError}</Alert> : null}

              <Button
                type="submit"
                fullWidth
                size="lg"
                loading={ask.isPending}
                leadingIcon={<Icon name="send" size={18} />}
                disabled={!question.trim() || !documentId}
              >
                Ask
              </Button>
            </form>
          )}

          <div className="mt-5 rounded-[var(--radius-card)] border border-line bg-ink-50 p-3.5">
            <p className="flex items-center gap-2 text-[13px] font-semibold text-ink-800">
              <Icon name="shield" size={15} className="text-ink-400" />
              What this will not do
            </p>
            <ul className="mt-2 space-y-1.5 text-[13px] leading-relaxed text-ink-600">
              <li>It will not tell you what is wrong or what to do about it.</li>
              <li>It will not guess when the document is unclear — it will say so instead.</li>
              <li>It will not answer from anything other than the document you picked.</li>
            </ul>
          </div>
        </Card>

        <div className="space-y-4">
          {ask.isPending ? (
            <Card padding="md">
              <div className="flex items-center gap-3">
                <LoadingState label="Reading your document" variant="inline" />
                <span className="text-sm text-ink-500">Searching the document for an answer…</span>
              </div>
            </Card>
          ) : null}

          {answer ? (
            <AIMessage
              answer={answer.answer}
              supported={answer.supported}
              needsReview={answer.needs_review}
              safetyFlags={answer.safety_flags}
            >
              {answer.sources.length > 0 ? (
                <SourceList sources={answer.sources} />
              ) : (
                <p className="text-[12px] text-ink-400">
                  No passages were cited for this answer.
                </p>
              )}
              <p className="mt-3 font-mono text-[10px] text-ink-300">
                trace {answer.trace_id}
                {answer.llm_provider ? ` · ${answer.llm_provider}` : ''}
              </p>
            </AIMessage>
          ) : !ask.isPending ? (
            <EmptyState
              icon={<Icon name="sparkle" size={24} />}
              title="No question asked yet"
              description={
                selectedDocument
                  ? `Choose a question about ${selectedDocument.original_filename} and CareLoop will answer from that document, showing its sources.`
                  : 'Pick a document and ask a question. CareLoop answers from the document only, and shows the page for every claim.'
              }
            />
          ) : null}
        </div>
      </div>
    </div>
  )
}
