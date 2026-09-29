import { useState } from 'react'
import { Alert } from '@/components/Alert'
import { Card, CardHeader } from '@/components/Card'
import { EmptyState } from '@/components/EmptyState'
import { ErrorState } from '@/components/ErrorState'
import { Icon } from '@/components/Icon'
import { LoadingState } from '@/components/LoadingState'
import { NotificationItem } from '@/components/NotificationItem'
import { Select } from '@/components/Select'
import { PageHeader } from '@/layouts/AppShell'
import { useActivePatient } from '@/features/auth/useActivePatient'
import { useNotifications } from '@/hooks/usePatientData'
import { ENUM_LABELS, type NotificationStatus } from '@/types/api'

const STATUS_OPTIONS = [
  { value: '', label: 'All statuses' },
  ...(Object.entries(ENUM_LABELS.notificationStatus) as Array<[NotificationStatus, string]>).map(
    ([value, label]) => ({ value, label }),
  ),
]

export function NotificationsPage() {
  const { patientId } = useActivePatient()
  const [status, setStatus] = useState('')

  // Status is the only filter the backend supports for this list. A
  // type filter is deliberately absent rather than faked client-side, which
  // would quietly ignore anything past the first page.
  const notifications = useNotifications(patientId, status ? { status } : {})

  if (!patientId) {
    return (
      <div className="px-4 py-6 sm:px-6 lg:px-8">
        <LoadingState label="Loading patient" />
      </div>
    )
  }

  const items = notifications.data?.notifications ?? []
  const failed = items.filter((item) => item.status === 'failed')
  const pending = items.filter((item) => item.status === 'pending' || item.status === 'sending')

  return (
    <div className="px-4 py-6 sm:px-6 lg:px-8">
      <PageHeader
        title="Notifications"
        description="Every reminder and alert CareLoop has sent for this patient, and whether it was delivered."
      />

      {failed.length > 0 ? (
        <Alert tone="danger" className="mb-4" title={`${failed.length} message${failed.length === 1 ? '' : 's'} could not be delivered`}>
          Delivery problems are usually temporary and are retried automatically. If this persists,
          ask your care team to check the contact number on file.
        </Alert>
      ) : null}

      {pending.length > 0 ? (
        <Alert tone="brand" className="mb-4">
          {pending.length} message{pending.length === 1 ? ' is' : 's are'} still being sent.
        </Alert>
      ) : null}

      <div className="mb-4 flex flex-col gap-3 sm:flex-row">
        <Select
          label="Status"
          options={STATUS_OPTIONS}
          value={status}
          onChange={(event) => setStatus(event.target.value)}
          containerClassName="sm:w-56"
        />
      </div>

      <Card padding="md">
        <CardHeader
          title="Message history"
          description={
            notifications.data
              ? `${notifications.data.count} total, showing ${notifications.data.notifications.length}`
              : undefined
          }
          icon={<Icon name="bell" size={18} />}
        />
        <div className="mt-3">
          {notifications.isError ? (
            <ErrorState error={notifications.error} onRetry={() => void notifications.refetch()} compact />
          ) : notifications.isLoading ? (
            <LoadingState label="Loading notifications" />
          ) : items.length === 0 ? (
            <EmptyState
              icon={<Icon name="inbox" size={24} />}
              title="No messages yet"
              description="Reminders for medicines, appointments and your daily check-in will appear here once they are scheduled."
            />
          ) : (
            <ul className="space-y-2.5">
              {items.map((notification) => (
                <NotificationItem key={notification.id} notification={notification} />
              ))}
            </ul>
          )}
        </div>
      </Card>
    </div>
  )
}
