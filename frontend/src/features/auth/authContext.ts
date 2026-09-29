import { createContext } from 'react'

export type AuthStatus = 'loading' | 'authenticated' | 'unauthenticated'

export interface AuthContextValue {
  status: AuthStatus
  /** True once a token exists. The token itself is deliberately not exposed. */
  hasToken: boolean
  /** Patient ids the token grants access to, used to scope the whole app. */
  patientIds: string[]
  signIn: (token: string) => Promise<void>
  signOut: () => void
  /** Reports that the backend rejected the current token. */
  invalidate: () => void
}

export const AuthContext = createContext<AuthContextValue | null>(null)
