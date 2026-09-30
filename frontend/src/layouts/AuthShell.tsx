import type { ReactNode } from 'react'
import { CareLoopEmblem, CareLoopLogo } from '@/components/brand/CareLoopLogo'
import { CareLoopIllustration } from '@/components/brand/CareLoopIllustration'

/**
 * Frame for the public sign-in / sign-up screens: a premium split-screen auth
 * layout. On desktop a deep-teal brand panel carries the CareLoop identity,
 * supporting copy and the calm abstract loop illustration on the left, while
 * the elevated auth card sits on the canvas-colored right.
 *
 * Responsive:
 *  - `lg` and up : split screen (brand panel ≈ 46%, auth card column)
 *  - below `lg`   : stacked — compact logo → form card → small brand strip
 *
 * The brand copy lives here so both auth screens can never drift apart.
 */
export function AuthShell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-dvh bg-canvas lg:grid lg:grid-cols-[minmax(0,46fr)_minmax(0,54fr)]">
      {/* Brand panel — desktop only. */}
      <aside
        className="relative hidden flex-col justify-between overflow-hidden p-10 text-white lg:flex xl:p-14"
        style={{ background: 'linear-gradient(155deg, #0b4f5c 0%, #11536a 50%, #176b87 100%)' }}
      >
        {/* Soft light blooms, drawn from the emblem's aqua. */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -right-28 -top-28 size-[26rem] rounded-full bg-[#19c3c8]/10 blur-3xl"
        />
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -bottom-36 -left-24 size-[30rem] rounded-full bg-[#0b4f5c]/50 blur-3xl"
        />

        {/* Very subtle abstract flowing loops inspired by the CareLoop emblem. */}
        <svg
          aria-hidden="true"
          viewBox="0 0 640 640"
          preserveAspectRatio="xMidYMid slice"
          className="pointer-events-none absolute inset-0 h-full w-full"
          fill="none"
        >
          <g stroke="rgba(255, 255, 255, 0.07)" strokeLinecap="round">
            <circle cx="620" cy="40" r="170" strokeWidth="1.5" />
            <circle cx="620" cy="40" r="228" strokeWidth="1" opacity="0.6" />
            <circle cx="30" cy="630" r="130" strokeWidth="1.5" />
            <circle cx="30" cy="630" r="190" strokeWidth="1" opacity="0.6" />
          </g>
          <g stroke="rgba(25, 195, 200, 0.12)" strokeWidth="2" strokeLinecap="round">
            <path d="M 540 560 a 88 88 0 0 1 66 -150" />
            <path d="M 120 110 a 84 84 0 0 0 -38 92" />
          </g>
        </svg>

        <div className="relative z-10">
          <CareLoopLogo variant="compact" tone="dark" size={52} />
        </div>

        <div className="relative z-10 my-auto w-full pb-4">
          <h2 className="max-w-md text-2xl font-semibold leading-tight tracking-tight text-balance sm:text-[28px]">
            Human + AI Recovery Companion
          </h2>
          <p className="mt-3 max-w-md text-[15px] leading-relaxed text-white/70">
            Stay connected to your recovery with clear guidance, timely reminders, and daily
            check-ins.
          </p>
          <CareLoopIllustration size={288} className="mx-auto mt-8" />
        </div>

        <div className="relative z-10">
          <p className="max-w-xs text-[12px] leading-relaxed text-white/50">
            CareLoop summarises documents. It does not diagnose or replace your care team.
          </p>
          <p className="mt-4 text-[11px] font-semibold uppercase tracking-[0.24em] text-white/60">
            Calm Clinical Intelligence
          </p>
        </div>
      </aside>

      {/* Auth card — the elevated form surface on the canvas background. */}
      <main className="flex items-center justify-center px-4 py-10 sm:px-8 lg:py-16">
        <div className="w-full max-w-[27rem]">
          <div className="mb-7 flex justify-center lg:hidden">
            <CareLoopLogo variant="compact" tone="light" size={34} />
          </div>

          <div className="rounded-[var(--radius-auth-card)] border border-line bg-surface p-6 shadow-[0_2px_4px_-2px_rgb(16_24_40/0.06),0_16px_36px_-16px_rgb(16_24_40/0.16)] sm:p-8">
            {children}
          </div>

          <div className="mt-8 flex flex-col items-center gap-2.5 lg:hidden" aria-hidden="true">
            <CareLoopEmblem size={26} />
            <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-ink-400">
              Calm Clinical Intelligence
            </p>
          </div>
        </div>
      </main>
    </div>
  )
}