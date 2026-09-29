import { RouterProvider } from 'react-router-dom'
import { QueryClientProvider } from '@tanstack/react-query'
import { AuthProvider } from '@/features/auth/AuthProvider'
import { ActivePatientProvider } from '@/features/auth/ActivePatientProvider'
import { queryClient } from '@/lib/queryClient'
import { router } from '@/routes/router'

/**
 * Provider order matters and is easy to get wrong:
 *
 *   QueryClientProvider   every hook below needs a client
 *     └ AuthProvider      owns the token and the 401 handler
 *         └ ActivePatientProvider  resolves the selected patient, and needs auth
 *             └ Router    every screen reads the active patient
 */
export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <ActivePatientProvider>
          <RouterProvider router={router} />
        </ActivePatientProvider>
      </AuthProvider>
    </QueryClientProvider>
  )
}

export default App
