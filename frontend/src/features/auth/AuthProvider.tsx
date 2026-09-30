import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { authApi } from '@/api'
import { configureApiAuth, ApiError } from '@/lib/apiClient'
import { clearSession, readToken, writeToken } from '@/lib/tokenStore'
import type { AuthUser } from '@/types/api'
import { AuthContext, type AuthContextValue, type AuthStatus } from './authContext'

/**
 * Owns the session and wires the HTTP client to it.
 *
 * Sign-in is a credential exchange (`POST /auth/login`, or `/auth/register`
 * when creating an account) followed by `GET /auth/me`, which resolves the
 * account and its self-granted patient from the backend. The token itself is
 * an implementation detail: it is written to session storage, attached to
 * every request by the API client, and never surfaced to the user.
 */

/** Patient-facing copy for auth failures, kept in the AuthError shape. */
const CONNECTION_MESSAGE = "We couldn't connect to CareLoop. Please try again."
const LOGIN_FAILED_MESSAGE = 'Email or password is incorrect.'
const DUPLICATE_EMAIL_MESSAGE = 'An account with this email already exists.'

/**
 * Re-key an auth API failure into patient-safe copy. Connection failures and
 * the two well-known statuses get their own message; anything else passes
 * through as the API client produced it (422 field issues survive for the
 * form to render).
 */
function translateAuthError(cause: unknown, knownStatus: (status: number) => string | null): ApiError {
  if (cause instanceof ApiError) {
    if (cause.isNetworkError) {
      return new ApiError({
        status: 0,
        code: 'network_error',
        userMessage: CONNECTION_MESSAGE,
        isNetworkError: true,
      })
    }
    const mapped = knownStatus(cause.status)
    if (mapped) {
      return new ApiError({
        status: cause.status,
        code: cause.code,
        userMessage: mapped,
        issues: cause.issues,
        isNetworkError: cause.isNetworkError,
        traceId: cause.traceId,
      })
    }
    return cause
  }
  return new ApiError({ status: 0, code: 'unknown', userMessage: CONNECTION_MESSAGE, isNetworkError: true })
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [status, setStatus] = useState<AuthStatus>(() => (readToken() ? 'loading' : 'unauthenticated'))
  const [user, setUser] = useState<AuthUser | null>(null)

  const clear = useCallback(() => {
    clearSession()
    setUser(null)
    setStatus('unauthenticated')
    // Drop every cached response: it belongs to a session that no longer exists.
    queryClient.clear()
  }, [queryClient])

  // Configure the client once. The token getter reads the store on every call,
  // and the 401 handler only needs `setStatus`, so neither goes stale.
  useEffect(() => {
    configureApiAuth({
      getToken: () => readToken(),
      onUnauthorized: () => {
        setStatus((current) => (current === 'authenticated' ? 'unauthenticated' : current))
        setUser(null)
        clearSession()
        queryClient.clear()
      },
    })
  }, [queryClient])

  /**
   * Store a freshly issued token and resolve the account behind it. Shared by
   * signIn and signUp so both end in exactly the same authenticated state.
   */
  const establishSession = useCallback(async (token: string): Promise<AuthUser> => {
    writeToken(token)
    setStatus('loading')

    try {
      const me = await authApi.me()
      setUser(me)
      setStatus('authenticated')
      return me
    } catch (cause) {
      // The token was just issued but the backend refused it: discard it and
      // hand the failure to the caller so the form can render its error.
      clearSession()
      setUser(null)
      setStatus('unauthenticated')
      throw translateAuthError(cause, () => null)
    }
  }, [])

  const signIn = useCallback(
    async (email: string, password: string) => {
      const session = await authApi.login({ email, password }).catch((cause: unknown) => {
        throw translateAuthError(cause, (status) => (status === 401 ? LOGIN_FAILED_MESSAGE : null))
      })
      await establishSession(session.access_token)
    },
    [establishSession],
  )

  const signUp = useCallback(
    async (fullName: string, email: string, password: string) => {
      const session = await authApi.register({ full_name: fullName, email, password }).catch(
        (cause: unknown) => {
          throw translateAuthError(cause, (status) => (status === 409 ? DUPLICATE_EMAIL_MESSAGE : null))
        },
      )
      await establishSession(session.access_token)
    },
    [establishSession],
  )

  // Re-validate a token restored from sessionStorage. Without this a page
  // refresh would sit in `loading` forever, because nothing re-checks the
  // stored token and `RequireAuth` renders a spinner rather than redirecting.
  useEffect(() => {
    if (!readToken()) return
    let cancelled = false

    void (async () => {
      try {
        const me = await authApi.me()
        if (cancelled) return
        setUser(me)
        setStatus('authenticated')
      } catch {
        if (cancelled) return
        // A token that no longer works is discarded, so the sign-in screen
        // starts clean rather than replaying a dead credential.
        clearSession()
        setUser(null)
        setStatus('unauthenticated')
      }
    })()

    return () => {
      cancelled = true
    }
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      user,
      hasToken: Boolean(readToken()),
      signIn,
      signUp,
      signOut: clear,
      invalidate: clear,
    }),
    [status, user, signIn, signUp, clear],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}