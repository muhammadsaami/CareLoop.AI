import { Badge, type BadgeTone } from '@/components/Badge'
import { Card, CardHeader } from '@/components/Card'
import { Icon } from '@/components/Icon'
import { IconContainer } from '@/components/IconContainer'
import { cn } from '@/lib/cn'
import { useWarningSymptoms } from '@/hooks/usePatientData'
import { ENUM_LABELS, type SymptomSeverity } from '@/types/api'

export interface WarningSymptomsCardProps {
  patientId: string | null
  className?: string
}

const SEVERITY_TONE: Record<SymptomSeverity, BadgeTone> = {
  critical: 'danger',
  high: 'danger',
  medium: 'warning',
  low: 'neutral',
}

const SEVERITY_MARKER: Record<SymptomSeverity, string> = {
  critical: 'bg-danger-500',
  high: 'bg-danger-500',
  medium: 'bg-warning-500',
  low: 'bg-brand-400',
}

/**
 * Renders only warning symptoms that exist in the backend — they arrive from
 * the discharge document extraction, never from this component.
 */
export function WarningSymptomsCard({ patientId, className }: WarningSymptomsCardProps) {
  const symptoms = useWarningSymptoms(patientId)
  const list = symptoms.data ?? []
  const loading = symptoms.isLoading

  return (
    <Card padding="lg" as="section" className={cn('flex h-full flex-col', className)}>
      <CardHeader
        title="Warning symptoms"
        description="What your discharge plan asked you to watch for"
        icon={<IconContainer icon="alert" tone="danger" />}
      />

      <div className="mt-5 flex-1">
        {loading ? (
          <div className="space-y-3" aria-busy="true">
            {Array.from({ length: 3 }, (_, index) => (
              <div key={index} className="h-14 animate-pulse rounded-xl bg-ink-100" />
            ))}
          </div>
        ) : list.length === 0 ? (
          <div className="flex items-start gap-3 rounded-xl border border-dashed border-line-strong bg-surface-muted p-4">
            <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full bg-success-50 text-success-600">
              <Icon name="check" size={16} />
            </span>
            <div>
              <p className="text-sm font-semibold text-ink-800">No warning symptoms documented</p>
              <p className="mt-0.5 text-[13px] leading-relaxed text-ink-500">
                Warning symptoms from your discharge paperwork will appear here if any are recorded.
              </p>
            </div>
          </div>
        ) : (
          <ul className="space-y-2">
            {list.map((symptom) => (
              <li
                key={symptom.id}
                className="flex items-start justify-between gap-3 rounded-xl border border-line bg-surface px-4 py-3"
              >
                <div className="flex min-w-0 items-start gap-3">
                  <span
                    className={cn('mt-1.5 size-2 shrink-0 rounded-full', SEVERITY_MARKER[symptom.severity])}
                    aria-hidden="true"
                  />
                  <p className="text-sm font-medium leading-snug text-ink-800">{symptom.description}</p>
                </div>
                <Badge tone={SEVERITY_TONE[symptom.severity]} size="sm" className="shrink-0">
                  {ENUM_LABELS.symptomSeverity[symptom.severity]}
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  )
}