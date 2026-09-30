import { useState } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Alert } from '@/components/Alert'
import { Button } from '@/components/Button'
import { Icon } from '@/components/Icon'
import { Input } from '@/components/Input'
import { useAuth } from '@/features/auth/useAuth'
import { ApiError } from '@/lib/apiClient'
import { AuthShell } from '@/layouts/AuthShell'

const signUpSchema = z
  .object({
    full_name: z
      .string()
      .trim()
      .min(1, 'Enter your full name')
      .max(255, 'Full name must be 255 characters or fewer.'),
    email: z.string().trim().min(1, 'Enter your email address').email('Enter a valid email address'),
    password: z
      .string()
      .min(12, 'Password must be at least 12 characters.')
      .refine((value) => new TextEncoder().encode(value).length <= 72, {
        message: 'Password must be at most 72 characters.',
      }),
    confirm_password: z.string().min(1, 'Re-enter your password to confirm it.'),
  })
  .refine((data) => data.password === data.confirm_password, {
    message: 'Passwords do not match.',
    path: ['confirm_password'],
  })

type SignUpValues = z.input<typeof signUpSchema>

const CONNECTION_MESSAGE = "We couldn't connect to CareLoop. Please try again."

/**
 * Self-service account creation.
 *
 * Sends the exact `POST /api/v1/auth/register` payload (`full_name`, `email`,
 * `password`). The backend responds with a signed session, which is stored and
 * immediately resolved through `GET /api/v1/auth/me` before redirecting to the
 * dashboard. Server field issues (422) are rendered inline per field.
 */
export function SignUpPage() {
  const { status, signUp } = useAuth()
  const [showPassword, setShowPassword] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [fieldIssues, setFieldIssues] = useState<Record<string, string>>({})

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<SignUpValues>({
    resolver: zodResolver(signUpSchema),
    defaultValues: { full_name: '', email: '', password: '', confirm_password: '' },
  })

  const onSubmit = handleSubmit(async (values) => {
    setFormError(null)
    setFieldIssues({})
    try {
      await signUp(values.full_name, values.email, values.password)
    } catch (cause) {
      if (cause instanceof ApiError) {
        // Any backend 422 that slipped past client validation becomes an
        // inline field error, never a raw stack trace.
        const issues: Record<string, string> = {}
        for (const issue of cause.issues) {
          if (!issues[issue.field]) issues[issue.field] = issue.message
        }
        setFieldIssues(issues)
        setFormError(cause.userMessage)
      } else {
        setFormError(CONNECTION_MESSAGE)
      }
    }
  })

  if (status === 'authenticated') {
    return <Navigate to="/dashboard" replace />
  }

  return (
    <AuthShell>
      <h1 className="text-[26px] font-semibold tracking-tight text-ink-950 sm:text-[28px]">
        Create your account
      </h1>
      <p className="mt-1.5 text-[15px] text-ink-500">
        Set up your account to keep your discharge recovery information in one place.
      </p>

      {formError ? (
        <Alert tone="danger" className="mt-5">
          {formError}
        </Alert>
      ) : null}

      <form onSubmit={onSubmit} noValidate className="mt-6 space-y-5">
        <Input
          label="Full name"
          autoComplete="name"
          placeholder="Your name"
          error={errors.full_name?.message ?? fieldIssues.full_name}
          disabled={isSubmitting}
          size="lg"
          {...register('full_name')}
          required
        />
        <Input
          label="Email"
          type="email"
          autoComplete="email"
          placeholder="you@example.com"
          error={errors.email?.message ?? fieldIssues.email}
          disabled={isSubmitting}
          size="lg"
          {...register('email')}
          required
        />
        <Input
          label="Password"
          type={showPassword ? 'text' : 'password'}
          autoComplete="new-password"
          placeholder="At least 12 characters"
          hint="At least 12 characters, with a mix of letters and numbers."
          error={errors.password?.message ?? fieldIssues.password}
          disabled={isSubmitting}
          size="lg"
          {...register('password')}
          required
          trailingSlot={
            <button
              type="button"
              onClick={() => setShowPassword((visible) => !visible)}
              aria-label={showPassword ? 'Hide password' : 'Show password'}
              className="rounded-lg p-2 text-ink-500 transition-colors hover:bg-ink-100 hover:text-ink-700"
            >
              <Icon name={showPassword ? 'eyeOff' : 'eye'} size={18} />
            </button>
          }
        />
        <Input
          label="Confirm password"
          type={showConfirm ? 'text' : 'password'}
          autoComplete="new-password"
          placeholder="Repeat your password"
          error={errors.confirm_password?.message ?? fieldIssues.confirm_password}
          disabled={isSubmitting}
          size="lg"
          {...register('confirm_password')}
          required
          trailingSlot={
            <button
              type="button"
              onClick={() => setShowConfirm((visible) => !visible)}
              aria-label={showConfirm ? 'Hide password' : 'Show password'}
              className="rounded-lg p-2 text-ink-500 transition-colors hover:bg-ink-100 hover:text-ink-700"
            >
              <Icon name={showConfirm ? 'eyeOff' : 'eye'} size={18} />
            </button>
          }
        />
        <Button type="submit" size="xl" fullWidth loading={isSubmitting}>
          {isSubmitting ? 'Creating account…' : 'Create Account'}
        </Button>
      </form>

      <div className="mt-6">
        <p className="text-center text-sm text-ink-500">
          Already have an account?{' '}
          <Link to="/sign-in" className="font-semibold text-brand-700 transition-colors hover:text-brand-800">
            Sign in
          </Link>
        </p>
        <p className="mt-4 text-center text-[13px] text-ink-400">
          Your account is protected with secure authentication.
        </p>
      </div>
    </AuthShell>
  )
}