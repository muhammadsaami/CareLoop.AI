import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { cn } from '@/lib/cn'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'subtle'
export type ButtonSize = 'sm' | 'md' | 'lg'

const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    'bg-brand-700 text-white shadow-soft hover:bg-brand-800 active:bg-brand-900 disabled:bg-brand-300',
  secondary:
    'bg-surface text-ink-800 border border-line-strong shadow-soft hover:bg-ink-50 hover:border-ink-300 active:bg-ink-100',
  ghost: 'text-ink-700 hover:bg-ink-100 active:bg-ink-200',
  subtle: 'bg-brand-50 text-brand-800 hover:bg-brand-100 active:bg-brand-200',
  danger: 'bg-danger-600 text-white shadow-soft hover:bg-danger-700 active:bg-danger-900',
}

const SIZES: Record<ButtonSize, string> = {
  sm: 'h-8 px-3 text-[13px] gap-1.5 rounded-[var(--radius-control)]',
  md: 'h-10 px-4 text-sm gap-2 rounded-[var(--radius-control)]',
  lg: 'h-12 px-6 text-[15px] gap-2.5 rounded-[var(--radius-control)]',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
  fullWidth?: boolean
  leadingIcon?: ReactNode
  trailingIcon?: ReactNode
}

function Spinner({ className }: { className?: string }) {
  return (
    <svg
      className={cn('size-4 animate-spin', className)}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2.5" opacity="0.25" />
      <path
        d="M21 12a9 9 0 0 0-9-9"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
      />
    </svg>
  )
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'primary',
    size = 'md',
    loading = false,
    fullWidth = false,
    leadingIcon,
    trailingIcon,
    disabled,
    className,
    children,
    type = 'button',
    ...rest
  },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(
        'inline-flex items-center justify-center font-medium transition-colors duration-150',
        'disabled:cursor-not-allowed disabled:opacity-70',
        VARIANTS[variant],
        SIZES[size],
        fullWidth && 'w-full',
        className,
      )}
      {...rest}
    >
      {loading ? <Spinner /> : leadingIcon}
      {children}
      {!loading && trailingIcon}
    </button>
  )
})

export interface ButtonLinkProps {
  to: string
  variant?: ButtonVariant
  size?: ButtonSize
  fullWidth?: boolean
  leadingIcon?: ReactNode
  className?: string
  children: ReactNode
}

/** A router-aware button. Used so navigation does not do a full page load. */
export function ButtonLink({
  to,
  variant = 'primary',
  size = 'md',
  fullWidth = false,
  leadingIcon,
  className,
  children,
}: ButtonLinkProps) {
  return (
    <Link
      to={to}
      className={cn(
        'inline-flex items-center justify-center font-medium transition-colors duration-150',
        VARIANTS[variant],
        SIZES[size],
        fullWidth && 'w-full',
        className,
      )}
    >
      {leadingIcon}
      {children}
    </Link>
  )
}
