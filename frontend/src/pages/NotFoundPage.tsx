import { ButtonLink } from '@/components/Button'
import { EmptyState } from '@/components/EmptyState'
import { Icon } from '@/components/Icon'

export function NotFoundPage() {
  return (
    <div className="flex min-h-[60vh] items-center justify-center px-4">
      <EmptyState
        icon={<Icon name="search" size={24} />}
        title="This page does not exist"
        description="The link may be out of date, or the record it pointed to is no longer available."
        action={<ButtonLink to="/dashboard">Go to your dashboard</ButtonLink>}
      />
    </div>
  )
}
