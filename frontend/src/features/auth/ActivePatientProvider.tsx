import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { patientsApi } from '@/api'
import { ApiError } from '@/lib/apiClient'
import { readActivePatientId, writeActivePatientId } from '@/lib/tokenStore'
import { useAuth } from './useAuth'
import { ActivePatientContext, type ActivePatientContextValue } from './activePatientContext'

/**
 * Resolves which patient the app is showing.
 *
 * An account can hold several patient grants (`self`, `caregiver`,
 * `care_team`), and the backend has no "current patient" concept, so the choice
 * lives here and is remembered for the tab.
 */
export function ActivePatientProvider({ children }: { children: ReactNode }) {
  const { status } = useAuth()
  const [selectedId, setSelectedId] = useState<string | null>(() => readActivePatientId())

  const query = useQuery({
    queryKey: ['patients'],
    queryFn: ({ signal }) => patientsApi.list(signal),
    enabled: status === 'authenticated',
    staleTime: 60_000,
  })

  const patients = useMemo(() => query.data ?? [], [query.data])

  // The effective selection is DERIVED during render rather than synced from an
  // effect. The remembered id is honoured while the token still grants it, and
  // anything else resolves to the first granted patient. A stale remembered id
  // therefore cannot survive a sign-out: an empty patient list resolves to null,
  // so no reset effect is needed and no render sees a mismatched patient.
  const selectedIsGranted = selectedId !== null && patients.some((item) => item.id === selectedId)
  const patientId = selectedIsGranted ? selectedId : (patients[0]?.id ?? null)
  const patient = patientId === null ? null : (patients.find((item) => item.id === patientId) ?? null)

  // Storage is an external system, so persisting the resolved choice here is a
  // legitimate effect — it writes, it does not setState.
  useEffect(() => {
    if (patientId && patientId !== readActivePatientId()) {
      writeActivePatientId(patientId)
    }
  }, [patientId])

  const select = useCallback((nextPatientId: string) => {
    setSelectedId(nextPatientId)
    writeActivePatientId(nextPatientId)
  }, [])

  const refetch = useCallback(() => {
    void query.refetch()
  }, [query])

  const value = useMemo<ActivePatientContextValue>(
    () => ({
      patients,
      patient,
      patientId,
      isLoading: query.isLoading,
      isError: query.isError,
      error: query.error instanceof ApiError ? query.error.userMessage : null,
      select,
      refetch,
    }),
    [patients, patient, patientId, query.isLoading, query.isError, query.error, select, refetch],
  )

  return <ActivePatientContext.Provider value={value}>{children}</ActivePatientContext.Provider>
}
