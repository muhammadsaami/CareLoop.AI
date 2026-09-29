import { useEffect, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { cn } from '@/lib/cn'

export interface ModalProps {
  open: boolean
  onClose: () => void
  title: string
  description?: ReactNode
  children: ReactNode
  footer?: ReactNode
  size?: 'sm' | 'md' | 'lg'
  /** Blocks backdrop/Escape dismissal while a mutation is in flight. */
  busy?: boolean
}

const SIZES = { sm: 'max-w-md', md: 'max-w-lg', lg: 'max-w-2xl' }

/**
 * Accessible dialog.
 *
 * Handles the things a hand-rolled overlay usually gets wrong: focus moves in
 * on open and returns on close, Tab is trapped inside, Escape closes, the page
 * behind cannot scroll, and `aria-modal` + labelling are wired up.
 */
export function Modal({ open, onClose, title, description, children, footer, size = 'md', busy }: ModalProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  const previouslyFocused = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!open) return

    previouslyFocused.current = document.activeElement as HTMLElement | null
    const { overflow } = document.body.style
    document.body.style.overflow = 'hidden'

    // Focus the first sensible control, or the panel itself.
    const focusTimer = window.setTimeout(() => {
      const focusable = panelRef.current?.querySelector<HTMLElement>(
        'input:not([type="hidden"]), select, textarea, button, [href], [tabindex]:not([tabindex="-1"])',
      )
      ;(focusable ?? panelRef.current)?.focus()
    }, 0)

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape' && !busy) {
        event.preventDefault()
        onClose()
        return
      }
      if (event.key !== 'Tab') return

      const nodes = panelRef.current?.querySelectorAll<HTMLElement>(
        'input:not([type="hidden"]):not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
      )
      if (!nodes || nodes.length === 0) return
      const first = nodes[0]!
      const last = nodes[nodes.length - 1]!

      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', onKeyDown)
    return () => {
      window.clearTimeout(focusTimer)
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = overflow
      previouslyFocused.current?.focus()
    }
  }, [open, onClose, busy])

  if (!open) return null

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-end justify-center sm:items-center">
      <div
        className="absolute inset-0 animate-[fade-in_0.18s_ease-out] bg-ink-950/40 backdrop-blur-[2px]"
        onClick={() => !busy && onClose()}
        aria-hidden="true"
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-title"
        aria-describedby={description ? 'modal-description' : undefined}
        tabIndex={-1}
        className={cn(
          'relative z-10 flex max-h-[92dvh] w-full flex-col overflow-hidden bg-surface shadow-overlay',
          'animate-[rise_0.22s_var(--ease-calm)] rounded-t-2xl sm:rounded-[var(--radius-card)]',
          SIZES[size],
        )}
      >
        <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
          <div className="min-w-0">
            <h2 id="modal-title" className="text-[15px] font-semibold text-ink-900">
              {title}
            </h2>
            {description ? (
              <p id="modal-description" className="mt-1 text-[13px] text-ink-500">
                {description}
              </p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            aria-label="Close dialog"
            className="-mr-1 -mt-1 rounded-md p-1.5 text-ink-400 transition-colors hover:bg-ink-100 hover:text-ink-700 disabled:opacity-40"
          >
            <svg className="size-4" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" aria-hidden="true">
              <path d="m5.5 5.5 9 9m0-9-9 9" />
            </svg>
          </button>
        </div>

        <div className="scrollbar-thin flex-1 overflow-y-auto px-5 py-4">{children}</div>

        {footer ? (
          <div className="flex items-center justify-end gap-2 border-t border-line bg-surface-muted px-5 py-3.5">
            {footer}
          </div>
        ) : null}
      </div>
    </div>,
    document.body,
  )
}
