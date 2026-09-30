import { cn } from '@/lib/cn'

/**
 * Hand-rolled stroke icon set.
 *
 * A dependency would work, but these are the only ~20 glyphs the app needs and
 * inlining them keeps the bundle free of an icon package in a healthcare app
 * that ships as little third-party code as possible. All are 24×24, 1.75
 * stroke, round caps, and `aria-hidden` — meaning-only text sits beside them.
 */

const PATHS = {
  home: 'M3 10.5 12 3l9 7.5M5.5 9.5V20h13V9.5M9.5 20v-6h5v6',
  clipboard: 'M9 4.5h6M9 4.5a1.5 1.5 0 0 1 1.5-1.5h3A1.5 1.5 0 0 1 15 4.5M9 4.5H7.5A1.5 1.5 0 0 0 6 6v13.5A1.5 1.5 0 0 0 7.5 21h9a1.5 1.5 0 0 0 1.5-1.5V6a1.5 1.5 0 0 0-1.5-1.5H15M9 11h6M9 14.5h4',
  pill: 'M10.5 3.5a7 7 0 0 1 0 9.9 7 7 0 0 1-9.9 0 7 7 0 0 1 9.9-9.9ZM7.8 6.2 3.5 10.5a4 4 0 0 0 5.7 5.7l4.3-4.3',
  calendar: 'M4.5 6.5h15v14h-15v-14ZM8 3.5v4M16 3.5v4M4.5 11h15M9 14.5h2M13 14.5h2M9 17.5h2',
  file: 'M13.5 3.5H7A1.5 1.5 0 0 0 5.5 5v14A1.5 1.5 0 0 0 7 20.5h10a1.5 1.5 0 0 0 1.5-1.5V8.5l-5-5ZM13.5 3.5v5h5M9 13h6M9 16.5h4',
  sparkle: 'M12 3.5l1.8 4.7 4.7 1.8-4.7 1.8L12 16.5l-1.8-4.7L5.5 10l4.7-1.8L12 3.5ZM18 16l.9 2.1 2.1.9-2.1.9L18 22l-.9-2.1-2.1-.9 2.1-.9L18 16Z',
  users: 'M9 11.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7ZM3 20.5c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5M16 5a3.5 3.5 0 0 1 0 6.8M17.5 15.2c2.1.6 3.5 2.5 3.5 5.3',
  bell: 'M18 9a6 6 0 1 0-12 0c0 4.5-2 6-2 6h16s-2-1.5-2-6ZM10.3 19a2 2 0 0 0 3.4 0',
  settings:
    'M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4ZM19.4 14.4a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5v.2a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1h.2a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1Z',
  menu: 'M4 7h16M4 12h16M4 17h16',
  close: 'm6 6 12 12M18 6 6 18',
  search: 'M11 18.5a7.5 7.5 0 1 0 0-15 7.5 7.5 0 0 0 0 15ZM20.5 20.5l-4-4',
  upload: 'M12 15.5V4.5M8 8l4-4 4 4M4.5 15.5v3A1.5 1.5 0 0 0 6 20.5h12a1.5 1.5 0 0 0 1.5-1.5v-3',
  download: 'M12 4.5v11M8 12l4 4 4-4M4.5 15.5v3A1.5 1.5 0 0 0 6 20.5h12a1.5 1.5 0 0 0 1.5-1.5v-3',
  check: 'm5 12.5 4.5 4.5L19 7.5',
  alert: 'M12 8v4.5M12 15.5v.3M12 3.2 2.8 19a1.2 1.2 0 0 0 1 1.8h16.4a1.2 1.2 0 0 0 1-1.8L12 3.2Z',
  chevronRight: 'm9.5 5.5 6.5 6.5-6.5 6.5',
  chevronLeft: 'M14.5 5.5 8 12l6.5 6.5',
  chevronDown: 'm5.5 9 6.5 6.5L18.5 9',
  plus: 'M12 5v14M5 12h14',
  trash: 'M4.5 7h15M9.5 7V5.5A1.5 1.5 0 0 1 11 4h2a1.5 1.5 0 0 1 1.5 1.5V7M6.5 7l.8 12.1A1.5 1.5 0 0 0 8.8 20.5h6.4a1.5 1.5 0 0 0 1.5-1.4L17.5 7M10.5 11v5.5M13.5 11v5.5',
  edit: 'M4.5 19.5h4L19 9a2.1 2.1 0 0 0-3-3L5.5 16.5v3ZM14.5 7.5l2 2',
  logout: 'M9.5 4.5H6.5A1.5 1.5 0 0 0 5 6v12a1.5 1.5 0 0 0 1.5 1.5h3M15 8l4 4-4 4M19 12H9',
  shield: 'M12 3.5 5 6v5.5c0 4.3 2.9 8.3 7 9.5 4.1-1.2 7-5.2 7-9.5V6l-7-2.5ZM9.5 12l2 2 3.5-3.5',
  info: 'M12 11v5.5M12 7.8v.2M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z',
  clock: 'M12 7.5V12l3 2M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z',
  arrowLeft: 'M19 12H5M11 6l-6 6 6 6',
  send: 'M4.5 12 20 4.5 15 19.5l-3.5-6-7-1.5Z',
  refresh: 'M20 12a8 8 0 1 1-2.5-5.8M20 4v4.5h-4.5',
  filter: 'M4 6h16M7 12h10M10 18h4',
  phone: 'M6.5 3.5h3l1.5 4-2 1.5a10 10 0 0 0 6 6l1.5-2 4 1.5v3a1.5 1.5 0 0 1-1.6 1.5C10.5 18.5 5.5 13.5 5 5.1A1.5 1.5 0 0 1 6.5 3.5Z',
  eye: 'M2.5 12S6 6 12 6s9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6ZM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z',
  eyeOff: 'M4 4l16 16M9.9 5.2A9.6 9.6 0 0 1 12 5c6 0 9.5 6 9.5 6a17 17 0 0 1-2.7 3.3M6.3 7.5A17 17 0 0 0 2.5 11s3.5 6 9.5 6c1.2 0 2.3-.2 3.3-.6M9.5 9.8a3 3 0 0 0 4.2 4.2',
  inbox: 'M3.5 13.5h4l1.5 3h6l1.5-3h4M3.5 13.5 6 5.5h12l2.5 8v5a1.5 1.5 0 0 1-1.5 1.5H5a1.5 1.5 0 0 1-1.5-1.5v-5Z',
  layers: 'M12 3.5 3 8l9 4.5L21 8l-9-4.5ZM3 12.5l9 4.5 9-4.5M3 17l9 4.5 9-4.5',
  star: 'M12 4l2.4 5 5.6.8-4 3.9 1 5.5-5-2.7-5 2.7 1-5.5-4-3.9 5.6-.8L12 4Z',
  activity: 'M3 12h3.2l2.1-5.5 4.2 11 2.5-5.5H21',
  heart: 'M20.8 6.7a5.5 5.5 0 0 0-7.8 0l-1 1-1-1a5.5 5.5 0 0 0-7.8 7.8l1 1L12 21.2l7.8-7.7 1-1a5.5 5.5 0 0 0 0-7.8Z',
} as const

export type IconName = keyof typeof PATHS

export interface IconProps {
  name: IconName
  className?: string
  /** 16 for dense rows, 20 for nav, 24 for feature blocks. Any number works. */
  size?: number
}

export function Icon({ name, className, size = 20 }: IconProps) {
  return (
    <svg
      className={cn('shrink-0', className)}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d={PATHS[name]} />
    </svg>
  )
}
