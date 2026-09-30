import { createContext } from 'react'
import type { AuthUser } from '@/types/api'

export type AuthStatus = 'loading' | 'authenticated' | 'unauthenticated'

export interface AuthContextValue {
  status: AuthStatus
  /** The authenticated account, resolved from `/auth/me`. Null before sign-in. */
  user: AuthUser | null
  /** True once a session token exists. The token itself is deliberately not exposed. */
  hasToken: boolean
  signIn: (email: string, password: string) => Promise<void>
  signUp: (fullName: string, email: string, password: string) => Promise<void>
  signOut: () => void
  /** Reports that the backend rejected the current token. */
  invalidate: () => void
}

export const AuthContext = createContext<AuthContextValue | null>(null)