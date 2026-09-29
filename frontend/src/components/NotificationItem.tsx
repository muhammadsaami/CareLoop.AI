import { Badge, type BadgeTone } from './Badge'
import { cn } from '@/lib/cn'
import { formatDateTime } from '@/lib/format'
import { ENUM_LABELS, type DeliveryChannel, type Notification, type NotificationStatus } from '@/types/api'

const STATUS_TONE: Record<NotificationStatus, BadgeTone> = {
  pending: 'neutral',
  sending: 'info',
  sent: 'success',
  failed: 'danger',
  skipped: 'neutral',
}

export interface NotificationItemProps {
  notification: Notification
  onClick?: () => void
  className?: string
}

/**
 * One notification record.
 *
 * `recipient` is shown on purpose. Phase 6 forbids falling back to the
 * patient's own number when a caregiver alert should go to a caregiver, so the
 * user must be able to see which number a message actually went to.
 */
export function NotificationItem({ notification, onClick, className }: NotificationItemProps) {
  const failed = notification.status === 'failed'
  const channel: DeliveryChannel = notification.channel

  const content = (
    <>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-ink-900">{notification.body}</p>
          <p className="mt-1 text-[12px] text-ink-500">
            {ENUM_LABELS.notificationType[notification.notification_type] ??
              notification.notification_type}{' '}
            · {channel} · to {notification.recipient}
          </p>
        </div>
        <Badge
          tone={STATUS_TONE[notification.status] ?? 'neutral'}
          dot
        >
          {ENUM_LABELS.notificationStatus[notification.status] ?? notification.status}
        </Badge>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-ink-400">
        <span data-numeric>Scheduled {formatDateTime(notification.scheduled_for)}</span>
        {notification.sent_at ? (
          <span data-numeric>Sent {formatDateTime(notification.sent_at)}</span>
        ) : null}
        {notification.attempt_count > 1 ? (
          <span data-numeric>{notification.attempt_count} attempts</span>
        ) : null}
      </div>

      {failed && notification.last_error ? (
        <p className="mt-2 rounded-[var(--radius-control)] border border-danger-200 bg-danger-50 px-3 py-2 text-[13px] text-danger-700">
          {notification.last_error}
        </p>
      ) : null}
    </>
  )

  return (
    <li className={cn('list-none', className)}>
      {onClick ? (
        <button
          type="button"
          onClick={onClick}
          className="w-full rounded-[var(--radius-card)] border border-line bg-surface p-4 text-left shadow-soft transition-shadow hover:shadow-raised"
        >
          {content}
        </button>
      ) : (
        <div className="rounded-[var(--radius-card)] border border-line bg-surface p-4 shadow-soft">
          {content}
        </div>
      )}
    </li>
  )
}
