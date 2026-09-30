import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'
import { Icon } from '@/components/Icon'
import { CareLoopLogo } from '@/components/brand/CareLoopLogo'
import { healthApi } from '@/api'
import { cn } from '@/lib/cn'
import { formatDate } from '@/lib/format'
import { useActivePatient } from '@/features/auth/useActivePatient'
import { useAuth } from '@/features/auth/useAuth'
import { useDocuments, useNotifications } from '@/hooks/usePatientData'
import { BOTTOM_NAV_ITEMS, NAV_GROUPS, NAV_ITEMS } from '@/routes/navigation'

/**
 * Authenticated application shell — the Figma shell: a dark teal sidebar with
 * branding, the patient profile card, grouped navigation and a footer status
 * block, beside a light main column with a breadcrumb header.
 *
 * Responsive strategy:
 *  - `lg` and up   : persistent 272px dark sidebar + sticky header
 *  - `md`..`lg`   : header only, navigation via a slide-in dark drawer
 *  - below `md`    : header PLUS a fixed bottom bar for the four highest-value
 *                    destinations, everything else in the drawer
 */
export function AppShell() {
  const { patient, patients, patientId, select, isLoading } = useActivePatient()
  const { signOut, user } = useAuth()
  const [drawerOpen, setDrawerOpen] = useState(false)
  // Desktop sidebar can collapse to an icon rail; the logo follows it.
  const [collapsed, setCollapsed] = useState(false)

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

  const label = useCurrentLabel()
  const systemStatus = useSystemStatus()

  // Counts for the sidebar badges come from the same real queries the pages
  // use, so a navigation destination never shows a number it cannot back up.
  const documents = useDocuments(patientId)
  const notifications = useNotifications(patientId, {})
  const needsReviewCount = useMemo(
    () => (documents.data ?? []).filter((doc) => doc.extraction_status === 'needs_review').length,
    [documents.data],
  )
  const notificationCount = notifications.data?.notifications.length ?? 0
  const navCounts: Record<string, number> = {
    '/documents': needsReviewCount,
    '/notifications': notificationCount,
  }

  const avatarName = patient?.name ?? user?.full_name ?? null

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
        avatarName={avatarName}
        notificationCount={notificationCount}
        className="lg:hidden"
      />

      <div className="mx-auto flex w-full max-w-[110rem]">
        <aside
          className={cn(
            'sticky top-0 hidden h-dvh shrink-0 flex-col bg-sidebar text-white transition-[width] duration-200 ease-out lg:flex',
            collapsed ? 'w-[4.75rem]' : 'w-[17rem]',
          )}
        >
          <div
            className={cn(
              'flex h-20 shrink-0 items-center border-b border-white/10',
              collapsed ? 'justify-center' : 'gap-3 px-6',
            )}
          >
            {collapsed ? (
              <CareLoopLogo variant="icon" tone="dark" size={40} />
            ) : (
              <CareLoopLogo variant="full" tone="dark" size={44} />
            )}
          </div>

          <div className={cn('border-b border-white/10', collapsed ? 'p-2' : 'p-3')}>
            <PatientProfile
              patients={patients}
              patientId={patientId}
              onSelect={select}
              isLoading={isLoading}
              collapsed={collapsed}
            />
          </div>

          <nav aria-label="Main" className="scrollbar-thin flex-1 overflow-y-auto px-3 py-4">
            <NavMenu counts={navCounts} collapsed={collapsed} />
          </nav>

          <div className={cn('border-t border-white/10', collapsed ? 'space-y-2 p-2' : 'space-y-2 p-3')}>
            <button
              type="button"
              onClick={() => setCollapsed((value) => !value)}
              title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
              className={cn(
                'flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium text-white/75 transition-colors hover:bg-white/10 hover:text-white',
                collapsed && 'justify-center px-0',
              )}
            >
              <Icon name={collapsed ? 'chevronRight' : 'chevronLeft'} size={18} />
              {!collapsed ? <span>Collapse sidebar</span> : <span className="sr-only">Expand sidebar</span>}
            </button>
            <div
              className={cn(
                'flex items-center gap-2.5 rounded-xl bg-white/5 px-3 py-2.5 ring-1 ring-white/10',
                collapsed && 'justify-center px-0',
              )}
              title={collapsed ? statusLabel(systemStatus) : undefined}
            >
              <span
                className={cn(
                  'size-2 shrink-0 rounded-full',
                  systemStatus === 'operational'
                    ? 'bg-success-400'
                    : systemStatus === 'degraded'
                      ? 'bg-warning-400'
                      : 'bg-white/40',
                )}
                aria-hidden="true"
              />
              {!collapsed ? (
                <span className="truncate text-[13px] font-medium text-white/75">
                  {statusLabel(systemStatus)}
                </span>
              ) : null}
            </div>
            <button
              type="button"
              onClick={signOut}
              title="Sign out"
              className={cn(
                'flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium text-white/75 transition-colors hover:bg-white/10 hover:text-white',
                collapsed && 'justify-center px-0',
              )}
            >
              <Icon name="logout" size={18} />
              {!collapsed ? <span>Sign out</span> : <span className="sr-only">Sign out</span>}
            </button>
          </div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <Header
            label={label}
            avatarName={avatarName}
            notificationCount={notificationCount}
            className="hidden lg:flex"
          />
          <main id="main" className="min-w-0 flex-1 pb-24 md:pb-8">
            <Outlet />
          </main>
        </div>
      </div>

      <BottomBar />

      {drawerOpen ? (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div
            className="absolute inset-0 animate-[fade-in_0.18s_ease-out] bg-ink-950/50"
            onClick={() => setDrawerOpen(false)}
            aria-hidden="true"
          />
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Navigation"
            className="absolute inset-y-0 left-0 flex w-[18rem] max-w-[85vw] animate-[rise_0.22s_var(--ease-calm)] flex-col bg-sidebar shadow-overlay"
          >
            <div className="flex h-16 items-center justify-between border-b border-white/10 px-4">
              <div className="flex items-center gap-2.5">
                <CareLoopLogo variant="compact" tone="dark" size={34} />
              </div>
              <button
                type="button"
                onClick={() => setDrawerOpen(false)}
                aria-label="Close navigation"
                className="rounded-lg p-2 text-white/70 transition-colors hover:bg-white/10"
              >
                <Icon name="close" size={18} />
              </button>
            </div>

            <div className="border-b border-white/10 p-3">
              <PatientProfile
                patients={patients}
                patientId={patientId}
                onSelect={select}
                isLoading={isLoading}
              />
            </div>

            <nav aria-label="Main" className="scrollbar-thin flex-1 overflow-y-auto px-3 py-4">
              <NavMenu counts={navCounts} onNavigate={closeDrawer} />
            </nav>

            <div className="border-t border-white/10 p-3">
              <button
                type="button"
                onClick={signOut}
                className="flex w-full items-center justify-center gap-3 rounded-xl bg-white/10 px-3 py-3 text-sm font-semibold text-white transition-colors hover:bg-white/15"
              >
                <Icon name="logout" size={18} />
                Sign out
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  )
}

/** The current page's nav label, for the breadcrumb. */
function useCurrentLabel(): string | null {
  const { pathname } = useLocation()
  return useMemo(() => {
    const segment = pathname.split('/').filter(Boolean)[0] ?? ''
    return NAV_ITEMS.find((item) => item.to === `/${segment}`)?.label ?? null
  }, [pathname])
}

/** Reads the real backend readiness endpoint so the footer indicator is data. */
type SystemStatus = 'checking' | 'operational' | 'degraded'
function statusLabel(status: SystemStatus): string {
  return status === 'operational'
    ? 'All systems operational'
    : status === 'degraded'
      ? 'System status degraded'
      : 'Checking systems…'
}
function useSystemStatus(): SystemStatus {
  const [status, setStatus] = useState<SystemStatus>('checking')

  useEffect(() => {
    let active = true
    const check = async () => {
      try {
        const ready = await healthApi.readiness()
        if (!active) return
        setStatus(ready.status === 'ok' ? 'operational' : 'degraded')
      } catch {
        if (active) setStatus('degraded')
      }
    }
    void check()
    const id = window.setInterval(check, 60_000)
    return () => {
      active = false
      window.clearInterval(id)
    }
  }, [])

  return status
}

/** Initials avatar. Falls back to the service mark when no name is visible. */
function Avatar({ name }: { name: string | null }) {
  const initials = useMemo(() => {
    const words = (name ?? '').trim().split(/\s+/).filter(Boolean)
    if (words.length === 0) return 'CL'
    return words
      .slice(0, 2)
      .map((word) => word.charAt(0))
      .join('')
      .toUpperCase()
  }, [name])

  return (
    <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-brand-600 text-[13px] font-semibold tracking-tight text-white">
      {initials}
    </span>
  )
}

/** Notification bell with a real count badge. */
function BellButton({ count }: { count: number }) {
  return (
    <Link
      to="/notifications"
      aria-label="Notifications"
      className="relative rounded-lg p-2 text-ink-600 transition-colors hover:bg-ink-100"
    >
      <Icon name="bell" size={20} />
      {count > 0 ? (
        <span
          className="absolute right-0.5 top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-danger-500 px-1 text-[10px] font-semibold leading-none text-white"
          data-numeric
        >
          {count > 9 ? '9+' : count}
        </span>
      ) : null}
    </Link>
  )
}

/** Sticky breadcrumb header above the content column (desktop). */
function Header({
  label,
  avatarName,
  notificationCount,
  className,
}: {
  label: string | null
  avatarName: string | null
  notificationCount: number
  className?: string
}) {
  return (
    <header
      className={cn(
        'sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-line bg-surface/95 px-6 backdrop-blur supports-[backdrop-filter]:bg-surface/80 lg:px-8',
        className,
      )}
    >
      <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-2 text-sm">
        <Link to="/dashboard" className="font-medium text-ink-500 transition-colors hover:text-ink-700">
          CareLoop
        </Link>
        {label ? (
          <>
            <Icon name="chevronRight" size={14} className="shrink-0 text-ink-300" />
            <span className="min-w-0 truncate font-semibold text-ink-900">{label}</span>
          </>
        ) : null}
      </nav>
      <div className="ml-auto flex items-center gap-1.5">
        <BellButton count={notificationCount} />
        <Link
          to="/settings"
          aria-label="Open settings"
          className="rounded-full p-0.5 transition-opacity hover:opacity-80"
        >
          <Avatar name={avatarName} />
        </Link>
      </div>
    </header>
  )
}

/** Mobile/tablet header: menu, brand, bell and avatar. */
function TopBar({
  onOpenDrawer,
  avatarName,
  notificationCount,
  className,
}: {
  onOpenDrawer: () => void
  avatarName: string | null
  notificationCount: number
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
        className="rounded-lg p-2 text-ink-600 transition-colors hover:bg-ink-100"
      >
        <Icon name="menu" size={20} />
      </button>
      <div className="flex min-w-0 items-center gap-2.5">
        <CareLoopLogo variant="compact" tone="light" size={26} />
      </div>
      <div className="ml-auto flex items-center gap-1.5">
        <BellButton count={notificationCount} />
        <Link
          to="/settings"
          aria-label="Open settings"
          className="rounded-full p-0.5 transition-opacity hover:opacity-80"
        >
          <Avatar name={avatarName} />
        </Link>
      </div>
    </header>
  )
}

/** Grouped navigation, shared by the sidebar and the drawer so they never drift. */
function NavMenu({
  counts,
  onNavigate,
  collapsed = false,
}: {
  counts: Record<string, number>
  onNavigate?: () => void
  collapsed?: boolean
}) {
  return (
    <div>
      {NAV_GROUPS.map((group) => (
        <div key={group} className="mb-5 last:mb-0">
          {collapsed ? null : (
            <p className="px-3 pb-1.5 text-[11px] font-semibold uppercase tracking-wider text-white/45">
              {group}
            </p>
          )}
          <ul className="space-y-1">
            {NAV_ITEMS.filter((item) => item.group === group).map((item) => {
              const count = counts[item.to] ?? 0
              return (
                <li key={item.to} className="relative">
                  <NavLink
                    to={item.to}
                    onClick={onNavigate}
                    title={collapsed ? item.label : undefined}
                    className={({ isActive }) => darkNavItemClasses(isActive, collapsed)}
                  >
                    <Icon name={item.icon} size={18} />
                    {collapsed ? (
                      <span className="sr-only">{item.label}</span>
                    ) : (
                      <span className="min-w-0 flex-1 truncate">{item.label}</span>
                    )}
                  </NavLink>
                  {count > 0 ? (
                    <span
                      className={
                        collapsed
                          ? 'pointer-events-none absolute -right-0.5 top-0 rounded-full bg-brand-500 px-1.5 py-px text-[9px] font-bold leading-tight text-white ring-2 ring-sidebar'
                          : 'rounded-full bg-white/15 px-2 py-0.5 text-[11px] font-semibold text-white'
                      }
                      data-numeric
                    >
                      {count > 99 ? '99+' : count}
                    </span>
                  ) : null}
                </li>
              )
            })}
          </ul>
        </div>
      ))}
    </div>
  )
}

function darkNavItemClasses(isActive: boolean, collapsed: boolean): string {
  return cn(
    'flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-colors duration-150',
    collapsed ? 'justify-center px-0' : undefined,
    isActive ? 'bg-brand-600 text-white shadow-soft' : 'text-white/75 hover:bg-white/10 hover:text-white',
  )
}

/**
 * A signed-in account can hold several patient grants, so which patient is on
 * screen has to stay visible and changeable. `/auth/me` identifies the account,
 * but the switchable list still comes from `GET /patients`.
 */
function PatientProfile({
  patients,
  patientId,
  onSelect,
  isLoading,
  collapsed = false,
}: {
  patients: Array<{ id: string; name: string; discharge_date: string | null }>
  patientId: string | null
  onSelect: (id: string) => void
  isLoading: boolean
  collapsed?: boolean
}) {
  if (collapsed) {
    if (isLoading) {
      return <div className="mx-auto size-11 animate-pulse rounded-full bg-white/10" />
    }
    if (patients.length === 0) {
      return (
        <div
          className="mx-auto flex size-11 items-center justify-center rounded-full bg-white/10 text-xs font-semibold text-white/60"
          title="No patient access"
        >
          CL
        </div>
      )
    }
    const active = patients.find((item) => item.id === patientId) ?? patients[0]!
    return (
      <div className="flex justify-center" title={active.name}>
        <Avatar name={active.name} />
      </div>
    )
  }

  if (isLoading) {
    return <div className="h-[4.25rem] animate-pulse rounded-xl bg-white/10" />
  }

  if (patients.length === 0) {
    return (
      <div className="rounded-xl bg-white/5 px-3 py-3 ring-1 ring-white/10">
        <p className="text-sm font-medium text-white">No patient access</p>
        <p className="mt-0.5 text-xs text-white/55">Ask your care team to grant access.</p>
      </div>
    )
  }

  const active = patients.find((item) => item.id === patientId) ?? patients[0]!
  const multi = patients.length > 1

  return (
    <div className="rounded-xl bg-white/5 p-3 ring-1 ring-white/10">
      <div className="flex items-center gap-3">
        <Avatar name={active.name} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-white">{active.name}</p>
          {active.discharge_date ? (
            <p className="mt-0.5 truncate text-xs text-white/55">
              Discharged {formatDate(active.discharge_date)}
            </p>
          ) : (
            <p className="mt-0.5 text-xs text-white/55">CareLoop patient</p>
          )}
        </div>
        {multi ? <Icon name="chevronDown" size={16} className="shrink-0 text-white/45" /> : null}
      </div>

      {multi ? (
        <div className="mt-3">
          <label
            htmlFor="patient-switcher"
            className="block px-0.5 pb-1 text-[11px] font-semibold uppercase tracking-wider text-white/45"
          >
            Viewing
          </label>
          <div className="relative">
            <select
              id="patient-switcher"
              value={patientId ?? ''}
              onChange={(event) => onSelect(event.target.value)}
              className="h-10 w-full appearance-none rounded-lg border border-white/15 bg-white/10 pl-3 pr-8 text-sm font-medium text-white focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-400/40"
            >
              {patients.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
            <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-white/45">
              <Icon name="chevronDown" size={14} />
            </span>
          </div>
        </div>
      ) : null}
    </div>
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
                      isActive && 'bg-brand-600/10',
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
        <h1 className="text-2xl font-semibold tracking-tight text-ink-950 sm:text-[28px]">{title}</h1>
        {description ? <p className="mt-1 max-w-2xl text-sm text-ink-500">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap gap-2">{actions}</div> : null}
    </div>
  )
}