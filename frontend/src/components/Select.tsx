import { forwardRef, useId, type ReactNode, type SelectHTMLAttributes } from 'react'
import { cn } from '@/lib/cn'

export interface SelectOption {
  value: string
  label: string
  disabled?: boolean
}

export interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'children'> {
  label?: ReactNode
  hint?: ReactNode
  error?: string
  /** Backend enum members. Rendered as options in the order given. */
  options: SelectOption[]
  placeholder?: string
  containerClassName?: string
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { label, hint, error, options, placeholder, className, containerClassName, id, ...rest },
  ref,
) {
  const generatedId = useId()
  const selectId = id ?? generatedId

  return (
    <div className={cn('space-y-1.5', containerClassName)}>
      {label ? (
        <label htmlFor={selectId} className="block text-sm font-medium text-ink-800">
          {label}
        </label>
      ) : null}
      <div className="relative">
        <select
          ref={ref}
          id={selectId}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${selectId}-error` : hint ? `${selectId}-hint` : undefined}
          className={cn(
            'h-10 w-full appearance-none rounded-[var(--radius-control)] border bg-surface',
            'pl-3 pr-9 text-sm text-ink-900 transition-colors duration-150',
            'focus:outline-none focus:ring-2 focus:ring-brand-600/25 focus:border-brand-600',
            'disabled:cursor-not-allowed disabled:bg-ink-50',
            error
              ? 'border-danger-500 focus:border-danger-600 focus:ring-danger-600/25'
              : 'border-line-strong hover:border-ink-300',
            className,
          )}
          {...rest}
        >
          {placeholder ? (
            <option value="" disabled>
              {placeholder}
            </option>
          ) : null}
          {options.map((option) => (
            <option key={option.value} value={option.value} disabled={option.disabled}>
              {option.label}
            </option>
          ))}
        </select>
        <svg
          className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-ink-400"
          viewBox="0 0 20 20"
          fill="none"
          aria-hidden="true"
        >
          <path
            d="m6 8 4 4 4-4"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </div>
      {error ? (
        <p id={`${selectId}-error`} role="alert" className="text-[13px] font-medium text-danger-700">
          {error}
        </p>
      ) : hint ? (
        <p id={`${selectId}-hint`} className="text-[13px] text-ink-500">
          {hint}
        </p>
      ) : null}
    </div>
  )
})
