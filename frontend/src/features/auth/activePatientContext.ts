import { createContext } from 'react'
import type { Patient } from '@/types/api'

export interface ActivePatientContextValue {
  /** The granted patients the token can see. */
  patients: Patient[]
  /** The selected patient, or null before selection. */
  patient: Patient | null
  patientId: string | null
  isLoading: boolean
  isError: boolean
  /** Error message safe to render. */
  error: string | null
  select: (patientId: string) => void
  refetch: () => void
}

export const ActivePatientContext = createContext<ActivePatientContextValue | null>(null)
