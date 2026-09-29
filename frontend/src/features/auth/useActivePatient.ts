import { useContext } from 'react'
import { ActivePatientContext, type ActivePatientContextValue } from './activePatientContext'

/** The patient every screen in the app is scoped to. */
export function useActivePatient(): ActivePatientContextValue {
  const value = useContext(ActivePatientContext)
  if (!value) {
    throw new Error('useActivePatient must be used inside <ActivePatientProvider>')
  }
  return value
}
