import { forwardRef, useId, type InputHTMLAttributes, type ReactNode, type TextareaHTMLAttributes } from 'react'
import { cn } from '@/lib/cn'

const CONTROL_BASE =
  'w-full rounded-[var(--radius-control)] border bg-surface px-3 text-sm text-ink-900 ' +
  'placeholder:text-ink-400 transition-colors duration-150 ' +
  'focus:outline-none focus:ring-2 focus:ring-brand-600/25 focus:border-brand-600 ' +
  'disabled:cursor-not-allowed disabled:bg-ink-50 disabled:text-ink-500'

function controlState(invalid?: boolean): string {
  return invalid
    ? 'border-danger-500 focus:border-danger-600 focus:ring-danger-600/25'
    : 'border-line-strong hover:border-ink-300'
}

interface FieldShellProps {
  id: string
  label?: ReactNode
  hint?: ReactNode
  error?: string
  required?: boolean
  children: ReactNode
  className?: string
}

function FieldShell({ id, label, hint, error, required, children, className }: FieldShellProps) {
  return (
    <div className={cn('space-y-1.5', className)}>
      {label ? (
        <label htmlFor={id} className="block text-sm font-medium text-ink-800">
          {label}
          {required ? (
            <span className="ml-1 text-danger-600" aria-hidden="true">
              *
            </span>
          ) : null}
        </label>
      ) : null}
      {children}
      {error ? (
        <p id={`${id}-error`} role="alert" className="text-[13px] font-medium text-danger-700">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="text-[13px] text-ink-500">
          {hint}
        </p>
      ) : null}
    </div>
  )
}

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: ReactNode
  hint?: ReactNode
  error?: string
  leadingIcon?: ReactNode
  trailingSlot?: ReactNode
  containerClassName?: string
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { label, hint, error, leadingIcon, trailingSlot, className, containerClassName, id, ...rest },
  ref,
) {
  const generatedId = useId()
  const inputId = id ?? generatedId

  return (
    <FieldShell
      id={inputId}
      label={label}
      hint={hint}
      error={error}
      required={rest.required}
      className={containerClassName}
    >
      <div className="relative">
        {leadingIcon ? (
          <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-400">
            {leadingIcon}
          </span>
        ) : null}
        <input
          ref={ref}
          id={inputId}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${inputId}-error` : hint ? `${inputId}-hint` : undefined}
          className={cn(
            CONTROL_BASE,
            'h-10',
            controlState(Boolean(error)),
            leadingIcon && 'pl-9',
            trailingSlot && 'pr-10',
            className,
          )}
          {...rest}
        />
        {trailingSlot ? (
          <span className="absolute right-2 top-1/2 -translate-y-1/2">{trailingSlot}</span>
        ) : null}
      </div>
    </FieldShell>
  )
})

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: ReactNode
  hint?: ReactNode
  error?: string
  containerClassName?: string
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { label, hint, error, className, containerClassName, id, rows = 4, ...rest },
  ref,
) {
  const generatedId = useId()
  const inputId = id ?? generatedId

  return (
    <FieldShell
      id={inputId}
      label={label}
      hint={hint}
      error={error}
      required={rest.required}
      className={containerClassName}
    >
      <textarea
        ref={ref}
        id={inputId}
        rows={rows}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${inputId}-error` : hint ? `${inputId}-hint` : undefined}
        className={cn(CONTROL_BASE, 'resize-y py-2.5', controlState(Boolean(error)), className)}
        {...rest}
      />
    </FieldShell>
  )
})
