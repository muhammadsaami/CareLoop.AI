import { cn } from '@/lib/cn'
import { CareLoopEmblem } from '@/components/brand/CareLoopLogo'

/**
 * Calm abstract brand art for the auth panels.
 *
 * There is no mascot component or asset in the product, so instead of inventing
 * a character this reuses the exact CareLoop emblem geometry and wraps it in
 * soft concentric loop arcs — a quiet, premium recovery-companion motif that
 * stays on brand. Artefacts render at low opacity so the panel text stays the
 * hero.
 */
export function CareLoopIllustration({
  size = 288,
  className,
}: {
  size?: number
  className?: string
}) {
  return (
    <div
      role="img"
      aria-label="CareLoop AI recovery companion"
      className={cn('relative', className)}
      style={{ width: size, height: size }}
    >
      <div
        aria-hidden="true"
        className="absolute inset-0 rounded-full"
        style={{
          background:
            'radial-gradient(circle at 50% 42%, rgba(25, 195, 200, 0.18) 0%, rgba(25, 195, 200, 0.06) 42%, transparent 66%)',
        }}
      />
      <svg
        aria-hidden="true"
        viewBox="0 0 320 320"
        className="absolute inset-0 h-full w-full"
        fill="none"
      >
        <circle cx="160" cy="160" r="152" stroke="rgba(255, 255, 255, 0.06)" strokeWidth="1" />
        <circle cx="160" cy="160" r="138" stroke="rgba(53, 214, 214, 0.18)" strokeWidth="1.5" />
        <circle cx="160" cy="160" r="118" stroke="rgba(25, 195, 200, 0.12)" strokeWidth="1" />
        <path
          d="M 62 108 a 92 92 0 0 1 150 -38"
          stroke="rgba(255, 255, 255, 0.14)"
          strokeWidth="2"
          strokeLinecap="round"
        />
        <path
          d="M 258 216 a 96 96 0 0 1 -132 42"
          stroke="rgba(53, 214, 214, 0.16)"
          strokeWidth="2"
          strokeLinecap="round"
        />
        <circle cx="268" cy="122" r="3.2" fill="rgba(53, 214, 214, 0.55)" />
        <circle cx="62" cy="236" r="2.6" fill="rgba(255, 255, 255, 0.4)" />
      </svg>
      <div className="absolute inset-0 flex items-center justify-center">
        <CareLoopEmblem size={Math.round(size * 0.6)} />
      </div>
    </div>
  )
}