import { useState } from 'react'
import { Link, Navigate, useLocation } from 'react-router-dom'
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

const signInSchema = z.object({
  email: z.string().trim().min(1, 'Enter your email address').email('Enter a valid email address'),
  password: z.string().min(1, 'Enter your password'),
})

type SignInValues = z.input<typeof signInSchema>

const CONNECTION_MESSAGE = "We couldn't connect to CareLoop. Please try again."

/**
 * Credential sign-in.
 *
 * The form sends `email` + `password` to `POST /api/v1/auth/login`; on success
 * the session token is stored by the auth layer, `GET /api/v1/auth/me` resolves
 * the account, and the app redirects to the requested page (or the dashboard).
 */
export function SignInPage() {
  const { status, signIn } = useAuth()
  const location = useLocation()
  const [showPassword, setShowPassword] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<SignInValues>({
    resolver: zodResolver(signInSchema),
    defaultValues: { email: '', password: '' },
  })

  const onSubmit = handleSubmit(async (values) => {
    setFormError(null)
    try {
      await signIn(values.email, values.password)
    } catch (cause) {
      // The auth layer has already translated the failure to patient copy.
      setFormError(cause instanceof ApiError ? cause.userMessage : CONNECTION_MESSAGE)
    }
  })

  if (status === 'authenticated') {
    const from = (location.state as { from?: string } | null)?.from
    return <Navigate to={from ?? '/dashboard'} replace />
  }

  return (
    <AuthShell>
      <h1 className="text-[26px] font-semibold tracking-tight text-ink-950 sm:text-[28px]">
        Welcome back
      </h1>
      <p className="mt-1.5 text-[15px] text-ink-500">Sign in to continue your recovery journey.</p>

      {formError ? (
        <Alert tone="danger" className="mt-5">
          {formError}
        </Alert>
      ) : null}

      <form onSubmit={onSubmit} noValidate className="mt-6 space-y-5">
        <Input
          label="Email address"
          type="email"
          autoComplete="email"
          placeholder="you@example.com"
          error={errors.email?.message}
          disabled={isSubmitting}
          size="lg"
          {...register('email')}
          required
        />
        <Input
          label="Password"
          type={showPassword ? 'text' : 'password'}
          autoComplete="current-password"
          placeholder="Enter your password"
          error={errors.password?.message}
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
        <Button type="submit" size="xl" fullWidth loading={isSubmitting}>
          {isSubmitting ? 'Signing in…' : 'Sign In'}
        </Button>
      </form>

      <div className="mt-6">
        <p className="text-center text-sm text-ink-500">
          Don&apos;t have an account?{' '}
          <Link to="/sign-up" className="font-semibold text-brand-700 transition-colors hover:text-brand-800">
            Create an account
          </Link>
        </p>
        <p className="mt-4 text-center text-[13px] text-ink-400">
          Your account is protected with secure authentication.
        </p>
      </div>
    </AuthShell>
  )
}