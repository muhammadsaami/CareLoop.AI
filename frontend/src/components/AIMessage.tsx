import { Badge } from './Badge'
import { cn } from '@/lib/cn'
import type { ReactNode } from 'react'

/** Copy for `safety_flags` the Phase 4 agent can return. */
const SAFETY_FLAG_COPY: Record<string, string> = {
  no_retrieval_match: 'Nothing in the document matched the question',
  unsupported_by_sources: 'The answer was not supported by the document',
  fabricated_source: 'A citation could not be resolved to a real passage',
  medical_overreach: 'The draft answer went beyond what the document supports',
}

function describeFlag(flag: string): string {
  return SAFETY_FLAG_COPY[flag] ?? flag.replace(/_/g, ' ')
}

export interface AIMessageProps {
  answer: string | null
  supported: boolean
  needsReview: boolean
  safetyFlags: string[]
  children?: ReactNode
  className?: string
  label?: string
}

/**
 * Renders one grounded answer.
 *
 * The layout is deliberately inverted from a typical chat UI: the safety state
 * comes FIRST and the answer text after it. When the agent withheld an answer,
 * that must be the first thing read, not a footnote beneath a confident
 * paragraph. There is no streaming, no regenerate button and no "try again",
 * because a medical summary must not look like a creative task.
 */
export function AIMessage({
  answer,
  supported,
  needsReview,
  safetyFlags,
  children,
  className,
  label = 'CareLoop assistant',
}: AIMessageProps) {
  const withheld = answer === null || answer.trim() === ''

  return (
    <article
      className={cn(
        'rounded-[var(--radius-card)] border border-line bg-surface p-4 sm:p-5',
        className,
      )}
    >
      <header className="flex items-center gap-2.5">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-brand-700 text-white">
          <svg
            className="size-4"
            viewBox="0 0 20 20"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <circle cx="10" cy="10" r="3.2" />
            <path d="M10 2.6v2M10 15.4v2M17.4 10h-2M4.6 10h-2M15.2 4.8l-1.4 1.4M6.2 13.8l-1.4 1.4M15.2 15.2l-1.4-1.4M6.2 6.2 4.8 4.8" />
          </svg>
        </span>
        <div className="min-w-0">
          <p className="text-sm font-semibold text-ink-900">{label}</p>
          <p className="text-[12px] text-ink-500">Answers only from your discharge document</p>
        </div>
      </header>

      {withheld ? (
        <div className="mt-4 rounded-[var(--radius-control)] border border-warning-200 bg-warning-50 p-3.5">
          <p className="text-sm font-semibold text-warning-700">No answer was given</p>
          <p className="mt-1 text-[13px] leading-relaxed text-ink-600">
            CareLoop could not find this in your document, so it did not answer. Your discharge
            paperwork is the source of truth — if you need help with this question, contact your care
            team.
          </p>
        </div>
      ) : (
        <div className="mt-4 whitespace-pre-wrap text-[15px] leading-relaxed text-ink-800">{answer}</div>
      )}

      {needsReview || !supported ? (
        <div className="mt-4 rounded-[var(--radius-control)] border border-warning-200 bg-warning-50 p-3.5">
          <p className="text-[13px] font-semibold text-warning-700">Check this against your paperwork</p>
          <p className="mt-0.5 text-[13px] leading-relaxed text-ink-600">
            This is a summary of a document, not medical advice, and it may have missed something.
            Your discharge instructions and your care team come first.
          </p>
        </div>
      ) : null}

      {safetyFlags.length > 0 ? (
        <ul className="mt-3 flex flex-wrap gap-1.5">
          {safetyFlags.map((flag) => (
            <li key={flag}>
              <Badge tone="warning" size="sm" title={flag}>
                {describeFlag(flag)}
              </Badge>
            </li>
          ))}
        </ul>
      ) : null}

      {children ? <div className="mt-4 border-t border-line pt-4">{children}</div> : null}
    </article>
  )
}
