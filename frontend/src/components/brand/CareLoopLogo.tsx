import { useId } from 'react'
import { cn } from '@/lib/cn'

/**
 * CareLoop AI brand identity.
 *
 * A single, reusable lockup. There is exactly one logo definition in the app —
 * every page imports it from here instead of hand-rolling "CareLoop AI" text or
 * a duplicate mark, so the identity can never drift.
 *
 * Variants:
 *  - `full`    : emblem + wordmark + "CALM CLINICAL INTELLIGENCE" tagline
 *  - `compact` : emblem + wordmark (no tagline)
 *  - `icon`    : emblem only
 *
 * The emblem is an original geometric lockup — two interlocking gradient rings
 * (deep teal → aqua) that read as a calm heart silhouette, a pale mint heart
 * core with an integrated teal medical plus, and a bright aqua dot above-right
 * suggesting the patient/person. `tone` adjusts the wordmark for light (canvas)
 * or dark (sidebar/auth panel) surfaces.
 */

const DEEP_TEAL = '#0B4F5C'
const TEAL = '#176B87'
const AQUA = '#19C3C8'
const BRIGHT_AQUA = '#35D6D6'
const MINT = '#DDF5EC'

export type CareLoopLogoVariant = 'full' | 'compact' | 'icon'
export type CareLoopTone = 'light' | 'dark'

export interface CareLoopLogoProps {
  variant?: CareLoopLogoVariant
  /** Emblem size in pixels; the wordmark scales proportionally. */
  size?: number
  /** `full` renders the tagline unless explicitly disabled. */
  showTagline?: boolean
  /** Adjective for the surface the logo sits on. */
  tone?: CareLoopTone
  /** Stack the emblem above the wordmark instead of side by side. */
  stacked?: boolean
  className?: string
}

function point(cx: number, cy: number, r: number, degrees: number) {
  const t = (degrees * Math.PI) / 180
  return { x: cx + r * Math.cos(t), y: cy + r * Math.sin(t) }
}

/** Annular arc band with a small gap at its skipped angle, as an SVG path. */
function bandPath(
  cx: number,
  cy: number,
  rOuter: number,
  rInner: number,
  start: number,
  sweep: number,
): string {
  const s = point(cx, cy, rOuter, start)
  const e = point(cx, cy, rOuter, start + sweep)
  const is = point(cx, cy, rInner, start + sweep)
  const ie = point(cx, cy, rInner, start)
  const large = sweep > 180 ? 1 : 0
  const f = (n: number) => n.toFixed(2)
  return [
    `M ${f(s.x)} ${f(s.y)}`,
    `A ${f(rOuter)} ${f(rOuter)} 0 ${large} 1 ${f(e.x)} ${f(e.y)}`,
    `L ${f(is.x)} ${f(is.y)}`,
    `A ${f(rInner)} ${f(rInner)} 0 ${large} 0 ${f(ie.x)} ${f(ie.y)}`,
    'Z',
  ].join(' ')
}

/** The mark itself, as raw SVG so it can scale to any size. */
export function CareLoopEmblem({
  size = 36,
  className,
}: {
  size?: number
  className?: string
}) {
  // Reusable component: unique gradient ids per instance so two emblems on one
  // page never fight over a shared id.
  const uid = useId().replace(/:/g, '')
  const left = `${uid}-left`
  const right = `${uid}-right`
  const dot = `${uid}-dot`

  // Two interlocking rings whose outermost edges meet at a smooth point,
  // forming a heart-like silhouette. Each ring carries a small gap at one of
  // the two crossing points so they read as weaving through each other.
  const ringA = bandPath(25, 34, 15, 7.5, -40.2, 342)
  const ringB = bandPath(39, 32, 14, 7, 125.4, 342)

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      className={className}
      role="img"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <linearGradient id={left} x1="0" y1="1" x2="1" y2="0">
          <stop offset="0" stopColor={DEEP_TEAL} />
          <stop offset="0.55" stopColor={TEAL} />
          <stop offset="1" stopColor={AQUA} />
        </linearGradient>
        <linearGradient id={right} x1="1" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={BRIGHT_AQUA} />
          <stop offset="0.5" stopColor={AQUA} />
          <stop offset="1" stopColor={TEAL} />
        </linearGradient>
        <linearGradient id={dot} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor={BRIGHT_AQUA} />
          <stop offset="1" stopColor={AQUA} />
        </linearGradient>
      </defs>

      <path d={ringA} fill={`url(#${left})`} />
      <path d={ringB} fill={`url(#${right})`} />

      {/* Pale heart-shaped core where the two rings meet. */}
      <path
        d="M 33 41.8 C 26.6 36.2 26.6 31.8 29.9 31.8 C 31.4 31.8 31.6 32.8 33 34.4 C 34.4 32.8 34.6 31.8 36.1 31.8 C 39.4 31.8 39.4 36.2 33 41.8 Z"
        fill={MINT}
      />

      {/* Integrated teal medical plus. */}
      <path
        d="M 33 32.2 V 37.8 M 30.2 35 H 35.8"
        stroke={DEEP_TEAL}
        strokeWidth={2.6}
        strokeLinecap="round"
        fill="none"
      />

      {/* The patient/person dot, tucked above the right ring. */}
      <circle cx={46.5} cy={15.5} r={3.4} fill={`url(#${dot})`} />
      <circle cx={45.6} cy={14.6} r={1.05} fill="#ffffff" opacity="0.85" />
    </svg>
  )
}

export function CareLoopLogo({
  variant = 'full',
  size = 36,
  showTagline,
  tone = 'light',
  stacked = false,
  className,
}: CareLoopLogoProps) {
  const hasTagline = variant === 'full' && showTagline !== false
  const word = Math.max(15, Math.round(size * 0.5))
  const tagline = Math.max(8, Math.round(size * 0.18))
  const isDark = tone === 'dark'

  const care = isDark ? '#ffffff' : DEEP_TEAL
  const loop = isDark ? BRIGHT_AQUA : AQUA
  const ai = isDark ? 'rgba(255, 255, 255, 0.85)' : DEEP_TEAL
  const taglineColor = isDark ? 'rgba(255, 255, 255, 0.52)' : 'rgba(23, 107, 135, 0.85)'

  return (
    <div
      role="img"
      aria-label="CareLoop AI"
      className={cn(
        'flex items-center gap-3',
        stacked && 'flex-col items-start gap-2.5',
        className,
      )}
    >
      <CareLoopEmblem size={size} />
      {variant !== 'icon' ? (
        <div className="min-w-0">
          <p className="whitespace-nowrap leading-none" style={{ fontSize: word }}>
            <span className="font-semibold tracking-tight" style={{ color: care }}>
              Care
            </span>
            <span className="font-semibold tracking-tight" style={{ color: loop }}>
              Loop
            </span>
            <span
              className="font-light"
              style={{ color: ai, letterSpacing: '0.22em', marginLeft: '0.4em' }}
            >
              AI
            </span>
          </p>
          {hasTagline ? (
            <p
              className="mt-[0.45em] whitespace-nowrap font-medium uppercase leading-none"
              style={{ fontSize: tagline, letterSpacing: '0.12em', color: taglineColor }}
            >
              Calm Clinical Intelligence
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}