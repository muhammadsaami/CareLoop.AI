import { useEffect, useState, type ReactNode } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { BrandMark, Icon } from '@/components/Icon'
import { Button } from '@/components/Button'
import { cn } from '@/lib/cn'
import { formatDate } from '@/lib/format'
import { useActivePatient } from '@/features/auth/useActivePatient'
import { useAuth } from '@/features/auth/useAuth'
import { BOTTOM_NAV_ITEMS, NAV_GROUPS, NAV_ITEMS, navItemClasses } from '@/routes/navigation'

/**
 * Authenticated application shell.
 *
 * Responsive strategy, deliberately not "shrink the sidebar":
 *  - `lg` and up   : persistent 260px sidebar
 *  - `md`..`lg`   : top bar only, navigation via a slide-in drawer
 *  - below `md`    : top bar PLUS a fixed bottom bar for the four highest-value
 *                    destinations, with everything else in the drawer
 *
 * The bottom bar is thumb-reachable, which matters for an app used one-handed
 * at a bedside; the drawer exists so the navigation depth never exceeds five.
 */
export function AppShell() {
  const { patient, patients, patientId, select, isLoading } = useActivePatient()
  const { signOut } = useAuth()
  const [drawerOpen, setDrawerOpen] = useState(false)

  // The drawer closes on navigation because every link inside it closes it,
  // rather than from an effect watching the pathname. An effect would also
  // re-close a drawer a user had just opened on the same route.
  const closeDrawer = () => setDrawerOpen(false)

  useEffect(() => {
    if (!drawerOpen) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setDrawerOpen(false)
    }
    document.addEventListener('keydown', onKeyDown)
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = ''
    }
  }, [drawerOpen])

  return (
    <div className="min-h-dvh bg-canvas">
      <a
        href="#main"
        className="sr-only-focusable fixed left-4 top-4 z-50 rounded-[var(--radius-control)] bg-brand-700 px-4 py-2 text-sm font-medium text-white"
      >
        Skip to main content
      </a>

      <TopBar
        onOpenDrawer={() => setDrawerOpen(true)}
        patientName={patient?.name ?? null}
        onSignOut={signOut}
        className="lg:hidden"
      />

      <div className="mx-auto flex w-full max-w-[110rem]">
        <aside className="sticky top-0 hidden h-dvh w-[16.25rem] shrink-0 flex-col border-r border-line bg-surface lg:flex">
          <div className="flex h-16 items-center gap-2.5 border-b border-line px-5">
            <BrandMark className="text-brand-700" size={30} />
            <span className="text-[15px] font-semibold tracking-tight text-ink-900">CareLoop AI</span>
          </div>

          <div className="border-b border-line p-3">
            <PatientSwitcher
              patients={patients}
              patientId={patientId}
              onSelect={select}
              isLoading={isLoading}
            />
          </div>

          <nav aria-label="Main" className="scrollbar-thin flex-1 overflow-y-auto p-3">
            {NAV_GROUPS.map((group) => (
              <div key={group} className="mb-4 last:mb-0">
                <p className="px-3 pb-1.5 text-[11px] font-semibold uppercase tracking-wider text-ink-400">
                  {group}
                </p>
                <ul className="space-y-0.5">
                  {NAV_ITEMS.filter((item) => item.group === group).map((item) => (
                    <li key={item.to}>
                      <NavLink to={item.to} className={({ isActive }) => navItemClasses(isActive)}>
                        <Icon name={item.icon} size={18} />
                        {item.label}
                      </NavLink>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </nav>

          <div className="border-t border-line p-3">
            <button
              type="button"
              onClick={signOut}
              className="flex w-full items-center gap-3 rounded-[var(--radius-control)] px-3 py-2.5 text-sm font-medium text-ink-600 transition-colors hover:bg-ink-100 hover:text-ink-900"
            >
              <Icon name="logout" size={18} />
              Sign out
            </button>
          </div>
        </aside>

        <main id="main" className="min-w-0 flex-1 pb-24 md:pb-8">
          <Outlet />
        </main>
      </div>

      <BottomBar />

      {drawerOpen ? (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div
            className="absolute inset-0 animate-[fade-in_0.18s_ease-out] bg-ink-950/40"
            onClick={() => setDrawerOpen(false)}
            aria-hidden="true"
          />
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Navigation"
            className="absolute inset-y-0 left-0 flex w-[17rem] max-w-[85vw] animate-[rise_0.22s_var(--ease-calm)] flex-col bg-surface shadow-overlay"
          >
            <div className="flex h-16 items-center justify-between border-b border-line px-4">
              <div className="flex items-center gap-2.5">
                <BrandMark className="text-brand-700" size={28} />
                <span className="text-[15px] font-semibold text-ink-900">CareLoop AI</span>
              </div>
              <button
                type="button"
                onClick={() => setDrawerOpen(false)}
                aria-label="Close navigation"
                className="rounded-md p-2 text-ink-500 hover:bg-ink-100"
              >
                <Icon name="close" size={18} />
              </button>
            </div>

            <div className="border-b border-line p-3">
              <PatientSwitcher
                patients={patients}
                patientId={patientId}
                onSelect={select}
                isLoading={isLoading}
              />
            </div>

            <nav aria-label="Main" className="scrollbar-thin flex-1 overflow-y-auto p-3">
              {NAV_GROUPS.map((group) => (
                <div key={group} className="mb-4 last:mb-0">
                  <p className="px-3 pb-1.5 text-[11px] font-semibold uppercase tracking-wider text-ink-400">
                    {group}
                  </p>
                  <ul className="space-y-0.5">
                    {NAV_ITEMS.filter((item) => item.group === group).map((item) => (
                      <li key={item.to}>
                        <NavLink
                          to={item.to}
                          onClick={closeDrawer}
                          className={({ isActive }) => navItemClasses(isActive)}
                        >
                          <Icon name={item.icon} size={18} />
                          {item.label}
                        </NavLink>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </nav>

            <div className="border-t border-line p-3">
              <Button variant="ghost" fullWidth leadingIcon={<Icon name="logout" size={18} />} onClick={signOut}>
                Sign out
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  )
}

function TopBar({
  onOpenDrawer,
  patientName,
  onSignOut,
  className,
}: {
  onOpenDrawer: () => void
  patientName: string | null
  onSignOut: () => void
  className?: string
}) {
  return (
    <header
      className={cn(
        'sticky top-0 z-30 flex h-16 items-center gap-3 border-b border-line bg-surface/95 px-4 backdrop-blur supports-[backdrop-filter]:bg-surface/80',
        className,
      )}
    >
      <button
        type="button"
        onClick={onOpenDrawer}
        aria-label="Open navigation"
        className="rounded-md p-2 text-ink-600 transition-colors hover:bg-ink-100 lg:hidden"
      >
        <Icon name="menu" size={20} />
      </button>
      <div className="flex min-w-0 items-center gap-2.5">
        <BrandMark className="text-brand-700" size={26} />
        <span className="truncate text-[15px] font-semibold text-ink-900">
          {patientName ?? 'CareLoop AI'}
        </span>
      </div>
      <div className="ml-auto flex items-center gap-1">
        <NavLink
          to="/notifications"
          aria-label="Notifications"
          className="rounded-md p-2 text-ink-600 transition-colors hover:bg-ink-100"
        >
          <Icon name="bell" size={20} />
        </NavLink>
        <button
          type="button"
          onClick={onSignOut}
          aria-label="Sign out"
          className="rounded-md p-2 text-ink-600 transition-colors hover:bg-ink-100"
        >
          <Icon name="logout" size={20} />
        </button>
      </div>
    </header>
  )
}

function BottomBar() {
  return (
    <nav
      aria-label="Primary"
      className="fixed inset-x-0 bottom-0 z-30 border-t border-line bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden"
    >
      <ul className="grid grid-cols-4">
        {BOTTOM_NAV_ITEMS.map((item) => (
          <li key={item.to}>
            <NavLink
              to={item.to}
              className={({ isActive }) =>
                cn(
                  'flex h-16 flex-col items-center justify-center gap-1 text-[11px] font-medium transition-colors',
                  isActive ? 'text-brand-700' : 'text-ink-500',
                )
              }
            >
              {({ isActive }) => (
                <>
                  <span
                    className={cn(
                      'flex h-7 w-12 items-center justify-center rounded-full transition-colors',
                      isActive && 'bg-brand-50',
                    )}
                  >
                    <Icon name={item.icon} size={20} />
                  </span>
                  {item.shortLabel}
                </>
              )}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  )
}

/**
 * A single token can hold several patient grants, so which patient is on screen
 * has to be visible and changeable. There is no `/me` endpoint in the backend,
 * so `GET /patients` is the only list available.
 */
function PatientSwitcher({
  patients,
  patientId,
  onSelect,
  isLoading,
}: {
  patients: Array<{ id: string; name: string; discharge_date: string | null }>
  patientId: string | null
  onSelect: (id: string) => void
  isLoading: boolean
}) {
  if (isLoading) {
    return <div className="h-[3.25rem] animate-pulse rounded-[var(--radius-control)] bg-ink-100" />
  }

  if (patients.length === 0) {
    return (
      <div className="rounded-[var(--radius-control)] border border-line bg-ink-50 px-3 py-2.5">
        <p className="text-[12px] font-medium text-ink-600">No patient access</p>
        <p className="mt-0.5 text-[12px] text-ink-500">Ask your care team to grant access.</p>
      </div>
    )
  }

  if (patients.length === 1) {
    const only = patients[0]!
    return (
      <div className="rounded-[var(--radius-control)] border border-line bg-ink-50 px-3 py-2.5">
        <p className="truncate text-sm font-medium text-ink-900">{only.name}</p>
        {only.discharge_date ? (
          <p className="mt-0.5 text-[12px] text-ink-500">Discharged {formatDate(only.discharge_date)}</p>
        ) : null}
      </div>
    )
  }

  return (
    <div>
      <label htmlFor="patient-switcher" className="block px-1 pb-1 text-[11px] font-semibold uppercase tracking-wider text-ink-400">
        Viewing
      </label>
      <div className="relative">
        <select
          id="patient-switcher"
          value={patientId ?? ''}
          onChange={(event) => onSelect(event.target.value)}
          className="h-[3.25rem] w-full appearance-none rounded-[var(--radius-control)] border border-line-strong bg-surface pl-3 pr-9 text-sm font-medium text-ink-900 focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-600/25"
        >
          {patients.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </select>
        <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-ink-400">
          <Icon name="chevronDown" size={16} />
        </span>
      </div>
    </div>
  )
}

/** Shared page frame so every screen has the same rhythm and max width. */
export function PageHeader({
  title,
  description,
  actions,
  eyebrow,
}: {
  title: string
  description?: ReactNode
  actions?: ReactNode
  eyebrow?: ReactNode
}) {
  return (
    <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        {eyebrow ? <div className="mb-1.5">{eyebrow}</div> : null}
        <h1 className="text-[22px] font-semibold tracking-tight text-ink-950 sm:text-2xl">{title}</h1>
        {description ? <p className="mt-1 max-w-2xl text-sm text-ink-500">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap gap-2">{actions}</div> : null}
    </div>
  )
}
