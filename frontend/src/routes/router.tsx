/*
 * A route table is the one place where lazy component handles and the exported
 * `router` belong together; splitting them would add a file and hide the table.
 */
/* oxlint-disable react/only-export-components */
import { Suspense, lazy, type ReactNode } from 'react'
import { Navigate, createBrowserRouter, isRouteErrorResponse, useRouteError } from 'react-router-dom'
import { AppShell } from '@/layouts/AppShell'
import { RequireAuth } from '@/routes/RequireAuth'
import { ErrorState } from '@/components/ErrorState'
import { LoadingState } from '@/components/LoadingState'
import { SignInPage } from '@/pages/SignInPage'
import { SignUpPage } from '@/pages/SignUpPage'
import { NotFoundPage } from '@/pages/NotFoundPage'

/**
 * Route table.
 *
 * Screens are code-split so a first paint ships the dashboard only. The
 * check-in bundle in particular carries the form and the question renderer, and
 * a patient who never checks in from that device never downloads it.
 */
const DashboardPage = lazy(() =>
  import('@/pages/DashboardPage').then((m) => ({ default: m.DashboardPage })),
)
const CheckInPage = lazy(() =>
  import('@/pages/CheckInPage').then((m) => ({ default: m.CheckInPage })),
)
const MedicationsPage = lazy(() =>
  import('@/pages/MedicationsPage').then((m) => ({ default: m.MedicationsPage })),
)
const AppointmentsPage = lazy(() =>
  import('@/pages/AppointmentsPage').then((m) => ({ default: m.AppointmentsPage })),
)
const DocumentsPage = lazy(() =>
  import('@/pages/DocumentsPage').then((m) => ({ default: m.DocumentsPage })),
)
const DocumentReviewPage = lazy(() =>
  import('@/pages/DocumentReviewPage').then((m) => ({ default: m.DocumentReviewPage })),
)
const AssistantPage = lazy(() =>
  import('@/pages/AssistantPage').then((m) => ({ default: m.AssistantPage })),
)
const NotificationsPage = lazy(() =>
  import('@/pages/NotificationsPage').then((m) => ({ default: m.NotificationsPage })),
)
const CaregiverPage = lazy(() =>
  import('@/pages/CaregiverPage').then((m) => ({ default: m.CaregiverPage })),
)
const SettingsPage = lazy(() =>
  import('@/pages/SettingsPage').then((m) => ({ default: m.SettingsPage })),
)

/** Fallback for a code-split screen that has not arrived yet. */
function RouteFallback() {
  return (
    <div className="px-4 py-6 sm:px-6 lg:px-8">
      <LoadingState label="Loading page" variant="page" />
    </div>
  )
}

function screen(element: ReactNode) {
  return <Suspense fallback={<RouteFallback />}>{element}</Suspense>
}

export const router = createBrowserRouter([
  { path: '/sign-in', element: <SignInPage /> },
  { path: '/sign-up', element: <SignUpPage /> },
  {
    path: '/',
    element: (
      <RequireAuth>
        <AppShell />
      </RequireAuth>
    ),
    errorElement: <RouteError />,
    children: [
      { index: true, element: <Navigate to="/dashboard" replace /> },
      { path: 'dashboard', element: screen(<DashboardPage />) },
      { path: 'check-in', element: screen(<CheckInPage />) },
      { path: 'medications', element: screen(<MedicationsPage />) },
      { path: 'appointments', element: screen(<AppointmentsPage />) },
      { path: 'documents', element: screen(<DocumentsPage />) },
      { path: 'documents/:documentId', element: screen(<DocumentReviewPage />) },
      { path: 'assistant', element: screen(<AssistantPage />) },
      { path: 'notifications', element: screen(<NotificationsPage />) },
      { path: 'caregiver', element: screen(<CaregiverPage />) },
      { path: 'settings', element: screen(<SettingsPage />) },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
])

/**
 * Catches a render or loader error anywhere in the tree. A crashed screen in a
 * health app has to say something rather than showing a blank page.
 */
function RouteError() {
  const error = useRouteError()
  const resolved = isRouteErrorResponse(error)
    ? new Error(error.statusText || 'This page could not be loaded')
    : error instanceof Error
      ? error
      : new Error('This page could not be loaded')

  return (
    <div className="flex min-h-dvh items-center justify-center bg-canvas px-4">
      <ErrorState error={resolved} title="This screen could not load" />
    </div>
  )
}
