import { cn } from '@/lib/cn'
import type { IconName } from '@/components/Icon'

export interface NavItem {
  to: string
  label: string
  shortLabel: string
  icon: IconName
  /** Shown in the sidebar under this heading. */
  group: 'Overview' | 'Daily' | 'Records' | 'Support'
}

/**
 * The full navigation map, in one place so the sidebar and the mobile drawer
 * can never drift apart.
 *
 * `shortLabel` exists for the mobile bottom bar, where horizontal space forces
 * one-word labels.
 */
export const NAV_ITEMS: NavItem[] = [
  { to: '/dashboard', label: 'Dashboard', shortLabel: 'Home', icon: 'home', group: 'Overview' },
  { to: '/check-in', label: 'Daily check-in', shortLabel: 'Check-in', icon: 'clipboard', group: 'Daily' },
  { to: '/notifications', label: 'Notifications', shortLabel: 'Alerts', icon: 'bell', group: 'Daily' },
  { to: '/documents', label: 'Documents', shortLabel: 'Docs', icon: 'file', group: 'Records' },
  { to: '/medications', label: 'Medications', shortLabel: 'Meds', icon: 'pill', group: 'Records' },
  { to: '/appointments', label: 'Appointments', shortLabel: 'Visits', icon: 'calendar', group: 'Records' },
  { to: '/assistant', label: 'CareLoop assistant', shortLabel: 'Assistant', icon: 'sparkle', group: 'Support' },
  { to: '/caregiver', label: 'Care team', shortLabel: 'Care', icon: 'users', group: 'Support' },
  { to: '/settings', label: 'Settings', shortLabel: 'Settings', icon: 'settings', group: 'Support' },
]

/** The four items that fit the mobile bottom bar, in priority order. */
export const BOTTOM_NAV_ITEMS: NavItem[] = [
  NAV_ITEMS[0]!,
  NAV_ITEMS[1]!,
  NAV_ITEMS[4]!,
  NAV_ITEMS[3]!,
]

export const NAV_GROUPS: NavItem['group'][] = ['Overview', 'Daily', 'Records', 'Support']

export function navItemClasses(isActive: boolean): string {
  return cn(
    'flex items-center gap-3 rounded-[var(--radius-control)] px-3 py-2.5 text-sm font-medium transition-colors duration-150',
    isActive
      ? 'bg-brand-50 text-brand-800'
      : 'text-ink-600 hover:bg-ink-100 hover:text-ink-900',
  )
}
