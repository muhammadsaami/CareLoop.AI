import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { patientsApi } from '@/api'
import { configureApiAuth, ApiError } from '@/lib/apiClient'
import { clearSession, readActivePatientId, readToken, writeActivePatientId, writeToken } from '@/lib/tokenStore'
import { AuthContext, type AuthContextValue, type AuthStatus } from './authContext'

/**
 * Owns the session and wires the HTTP client to it.
 *
 * The token is validated exactly once, on sign-in and on first load, by
 * calling `GET /patients` — the cheapest authenticated endpoint that proves the
 * token both parses and carries a patient grant. This is also how the app
 * discovers which patients the token can see, since there is no `/me` endpoint.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [status, setStatus] = useState<AuthStatus>(() => (readToken() ? 'loading' : 'unauthenticated'))
  const [patientIds, setPatientIds] = useState<string[]>([])

  const clear = useCallback(() => {
    clearSession()
    setPatientIds([])
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
        clearSession()
        queryClient.clear()
      },
    })
  }, [queryClient])

  const signIn = useCallback(
    async (token: string) => {
      const trimmed = token.trim()
      if (!trimmed) throw new Error('Enter your access token to continue.')

      writeToken(trimmed)
      setStatus('loading')

      try {
        const patients = await patientsApi.list()
        const ids = patients.map((patient) => patient.id)
        setPatientIds(ids)

        // Remember the patient from last session only if it is still granted.
        const previous = readActivePatientId()
        if (previous && !ids.includes(previous)) {
          writeActivePatientId(null)
        }
        setStatus('authenticated')
      } catch (error) {
        clearSession()
        setStatus('unauthenticated')
        if (error instanceof ApiError) throw error
        throw new ApiError({
          status: 0,
          code: 'network_error',
          userMessage: 'Cannot reach the CareLoop service. Check your connection and try again.',
          isNetworkError: true,
        })
      }
    },
    [],
  )

  // Validate a token restored from sessionStorage. Without this a page refresh
  // would sit in `loading` forever, because nothing re-checks the stored token
  // and `RequireAuth` renders a spinner rather than redirecting.
  useEffect(() => {
    if (!readToken()) return
    let cancelled = false

    void (async () => {
      try {
        const patients = await patientsApi.list()
        if (cancelled) return
        setPatientIds(patients.map((item) => item.id))
        setStatus('authenticated')
      } catch {
        if (cancelled) return
        // A token that no longer works is discarded, so the sign-in screen
        // starts clean rather than replaying a dead credential.
        clearSession()
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
      hasToken: Boolean(readToken()),
      patientIds,
      signIn,
      signOut: clear,
      invalidate: clear,
    }),
    [status, patientIds, signIn, clear],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
