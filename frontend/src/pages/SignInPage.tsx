import { useState, type FormEvent } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { Alert } from '@/components/Alert'
import { BrandMark, Icon } from '@/components/Icon'
import { Button } from '@/components/Button'
import { Textarea } from '@/components/Input'
import { healthApi } from '@/api'
import { ApiError } from '@/lib/apiClient'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '@/features/auth/useAuth'

/**
 * Access-token sign-in.
 *
 * There is no password field, and that is deliberate rather than unfinished.
 * The backend exposes no `/login`, `/register`, `/refresh` or `/me` endpoint;
 * tokens are minted out-of-band by an operator running
 * `python -m app.cli.manage_access` and used as `Authorization: Bearer <jwt>`.
 * Inventing a credential form would POST to an endpoint that does not exist.
 *
 * So this screen does the only honest thing available: accept the issued
 * token, validate it against the authenticated API, and tell the user exactly
 * what to do if it fails. The backend dependency is documented in the report.
 */
export function SignInPage() {
  const { status, signIn } = useAuth()
  const location = useLocation()
  const [token, setToken] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showToken, setShowToken] = useState(false)

  // The only public endpoint. Used to distinguish "API unreachable" from
  // "token rejected", which are very different problems for the user.
  const health = useQuery({
    queryKey: ['health'],
    queryFn: () => healthApi.health(),
    retry: false,
    staleTime: 30_000,
  })

  if (status === 'authenticated') {
    const from = (location.state as { from?: string } | null)?.from
    return <Navigate to={from ?? '/dashboard'} replace />
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)

    if (!token.trim()) {
      setError('Paste the access token issued to you.')
      return
    }

    setSubmitting(true)
    try {
      await signIn(token)
      // The token is in sessionStorage now; drop it from component state so it
      // is not left sitting in a React devtools-visible closure.
      setToken('')
    } catch (cause) {
      setError(
        cause instanceof ApiError
          ? cause.userMessage
          : 'Could not sign in. Check the token and try again.',
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="grid min-h-dvh lg:grid-cols-2">
      {/* Brand panel — hidden on small screens so the form is not pushed down. */}
      <aside className="relative hidden flex-col justify-between overflow-hidden bg-brand-900 p-10 text-white lg:flex">
        <div
          className="pointer-events-none absolute -right-24 -top-24 size-80 rounded-full bg-brand-700/40 blur-3xl"
          aria-hidden="true"
        />
        <div
          className="pointer-events-none absolute -bottom-32 -left-16 size-96 rounded-full bg-brand-800/60 blur-3xl"
          aria-hidden="true"
        />

        <div className="relative flex items-center gap-3">
          <BrandMark className="text-white/95" size={34} />
          <span className="text-lg font-semibold tracking-tight">CareLoop AI</span>
        </div>

        <div className="relative max-w-md">
          <h2 className="text-3xl font-semibold leading-tight tracking-tight text-balance">
            Your discharge recovery, in one calm place.
          </h2>
          <p className="mt-4 text-[15px] leading-relaxed text-brand-100/90">
            Check in each day, keep track of your medicines and appointments, and ask questions of
            your own discharge paperwork — with every answer traced back to the page it came from.
          </p>

          <ul className="mt-8 space-y-3.5">
            {[
              { icon: 'clipboard' as const, text: 'A short daily check-in that takes under a minute' },
              { icon: 'pill' as const, text: 'Your medicines and reminders in one list' },
              { icon: 'sparkle' as const, text: 'Answers grounded in your document, never guessed' },
              { icon: 'users' as const, text: 'Your care team alerted when something changes' },
            ].map((item) => (
              <li key={item.text} className="flex items-start gap-3 text-[15px] text-brand-50">
                <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg bg-white/10 text-brand-100">
                  <Icon name={item.icon} size={16} />
                </span>
                {item.text}
              </li>
            ))}
          </ul>
        </div>

        <p className="relative flex items-start gap-2 text-[13px] leading-relaxed text-brand-200/80">
          <Icon name="shield" size={16} className="mt-0.5" />
          CareLoop summarises documents. It does not diagnose or replace your care team.
        </p>
      </aside>

      <main className="flex items-center justify-center px-5 py-10 sm:px-8">
        <div className="w-full max-w-md">
          <div className="mb-8 flex items-center gap-3 lg:hidden">
            <BrandMark className="text-brand-700" size={32} />
            <span className="text-lg font-semibold tracking-tight text-ink-900">CareLoop AI</span>
          </div>

          <h1 className="text-2xl font-semibold tracking-tight text-ink-950">Sign in to CareLoop</h1>
          <p className="mt-2 text-sm text-ink-500">
            Enter the access token issued to you by your care team.
          </p>

          {health.isError ? (
            <Alert tone="danger" title="Cannot reach the CareLoop service" className="mt-5">
              The API did not respond. Check that the backend is running, then reload this page.
            </Alert>
          ) : health.isSuccess ? (
            <div className="mt-5 flex items-center gap-2 rounded-[var(--radius-control)] border border-success-200 bg-success-50 px-3 py-2 text-[13px] text-success-700">
              <Icon name="check" size={15} />
              Service reachable
              <span className="text-success-900/60">· {health.data.status}</span>
            </div>
          ) : null}

          {error ? (
            <Alert tone="danger" className="mt-5">
              {error}
            </Alert>
          ) : null}

          <form onSubmit={onSubmit} className="mt-6 space-y-4">
            <Textarea
              label="Access token"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              placeholder="eyJhbGciOi..."
              rows={3}
              required
              autoComplete="off"
              spellCheck={false}
              disabled={submitting}
              className="font-mono text-[13px]"
              hint="A long string beginning with eyJ. It is stored for this browser tab only."
            />

            <label className="flex items-center gap-2 text-[13px] text-ink-600">
              <input
                type="checkbox"
                checked={showToken}
                onChange={(event) => setShowToken(event.target.checked)}
                className="size-4 rounded accent-[var(--color-brand-700)]"
              />
              Show token
            </label>

            <Button type="submit" size="lg" fullWidth loading={submitting}>
              Continue
            </Button>
          </form>

          <div className="mt-8 rounded-[var(--radius-card)] border border-line bg-surface p-4">
            <p className="flex items-center gap-2 text-[13px] font-semibold text-ink-800">
              <Icon name="info" size={15} className="text-ink-400" />
              Where do I get this token?
            </p>
            <p className="mt-2 text-[13px] leading-relaxed text-ink-500">
              CareLoop has no password sign-in. Your care team issues a time-limited access token for
              your record and sends it to you directly. It expires, and you can ask for a new one at
              any time.
            </p>
          </div>

          <p className="mt-6 text-center text-[12px] leading-relaxed text-ink-400">
            If you are a caregiver, use the token issued to you. It will show the patients you have
            been granted.
          </p>
        </div>
      </main>
    </div>
  )
}
