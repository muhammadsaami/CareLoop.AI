import { QueryClient } from '@tanstack/react-query'
import { ApiError } from './apiClient'

/**
 * One client for the whole app.
 *
 * Defaults are chosen for a patient-facing app rather than a generic dashboard:
 *
 *  - Clinical data must not be silently replaced. `staleTime` keeps a re-mount
 *    (navigating away and back) from re-fetching a medication list, and
 *    `refetchOnWindowFocus` is off because a background tab regaining focus
 *    should not swap content out from under someone mid-read.
 *  - Retries are limited to transport failures and 5xx. A 400, 403, 404 or 422
 *    will fail identically every time, and retrying it just delays the honest
 *    error state.
 *  - `gcTime` is short enough that a sign-out does not leave another patient's
 *    name sitting in memory for long.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 5 * 60_000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        if (failureCount >= 2) return false
        if (error instanceof ApiError) {
          if (error.isNetworkError) return true
          return error.status >= 500 || error.status === 429
        }
        return false
      },
      retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 4000),
    },
    mutations: {
      // Never auto-retry a write. A duplicated medication or a duplicated
      // check-in is a worse outcome than a visible failure.
      retry: false,
    },
  },
})
