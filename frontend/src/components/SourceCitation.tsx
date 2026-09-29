import { cn } from '@/lib/cn'
import type { GroundedSource, RetrievedChunk } from '@/types/api'

export interface SourceCitationProps {
  source: GroundedSource | RetrievedChunk
  /** 1-based display index, so a citation can read "[1]". */
  index?: number
  /** The retrieved text, when the caller has it. */
  text?: string | null
  className?: string
}

function isRetrieved(chunk: GroundedSource | RetrievedChunk): chunk is RetrievedChunk {
  return 'text' in chunk
}

/**
 * One real chunk of one real page.
 *
 * Phase 4 guarantees the chunk ids in an answer resolve to actual retrieved
 * chunks, so a citation here is evidence, not decoration. The page is shown
 * only when the backend reported one — an invented page number is unrepresentable
 * in this API, and `null` here means "page unknown", never "page 1".
 */
export function SourceCitation({ source, index, text, className }: SourceCitationProps) {
  const chunkText = text ?? (isRetrieved(source) ? source.text : null)
  const page = source.source_page

  return (
    <li
      className={cn(
        'rounded-[var(--radius-control)] border border-line bg-surface-muted px-3 py-2.5',
        className,
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-[12px] font-medium text-ink-500">
          {index !== undefined ? (
            <span className="inline-flex size-5 items-center justify-center rounded bg-brand-100 text-[11px] font-semibold text-brand-800">
              {index}
            </span>
          ) : null}
          {page ? (
            <span>Page {page}</span>
          ) : (
            <span className="text-ink-400">Page not recorded</span>
          )}
        </span>
        <span className="font-mono text-[10px] text-ink-300" title={source.chunk_id}>
          {source.chunk_id.slice(0, 8)}
        </span>
      </div>
      {chunkText ? (
        <p className="mt-1.5 line-clamp-4 text-[13px] leading-relaxed text-ink-600">{chunkText}</p>
      ) : (
        <p className="mt-1.5 text-[13px] italic text-ink-400">
          Passage text is not shown for this citation.
        </p>
      )}
    </li>
  )
}

/** The list wrapper, with an explicit count for screen readers. */
export function SourceList({
  sources,
  texts,
  className,
}: {
  sources: GroundedSource[]
  /** Optional chunk text keyed by chunk_id, for expandable passages. */
  texts?: Record<string, string>
  className?: string
}) {
  if (sources.length === 0) return null

  return (
    <div className={className}>
      <p className="text-[12px] font-semibold uppercase tracking-wide text-ink-400">
        {sources.length} {sources.length === 1 ? 'source' : 'sources'} from this document
      </p>
      <ul className="mt-2 space-y-2">
        {sources.map((source, position) => (
          <SourceCitation
            key={source.chunk_id}
            source={source}
            index={position + 1}
            text={texts?.[source.chunk_id] ?? null}
          />
        ))}
      </ul>
    </div>
  )
}
