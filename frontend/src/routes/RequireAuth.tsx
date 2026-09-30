import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { CareLoopLogo } from '@/components/brand/CareLoopLogo'
import { LoadingState } from '@/components/LoadingState'
import { useAuth } from '@/features/auth/useAuth'

/**
 * Gate for every authenticated route.
 *
 * `loading` is a real state, not a flash: on a page refresh the token is read
 * from sessionStorage and re-validated, and rendering a redirect during that
 * window would bounce an authenticated user to sign-in.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { status } = useAuth()
  const location = useLocation()

  if (status === 'loading') {
    return (
      <div className="flex min-h-dvh flex-col items-center justify-center gap-6 bg-canvas px-6">
        <CareLoopLogo variant="icon" tone="light" size={44} />
        <div className="w-full max-w-xs">
          <LoadingState label="Checking your access" variant="block" />
        </div>
      </div>
    )
  }

  if (status === 'unauthenticated') {
    // Remember where they were headed so sign-in can return them there.
    return <Navigate to="/sign-in" replace state={{ from: location.pathname }} />
  }

  return <>{children}</>
}
