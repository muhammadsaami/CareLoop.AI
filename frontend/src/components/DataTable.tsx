import { cn } from '@/lib/cn'
import type { ReactNode } from 'react'

export interface Column<T> {
  key: string
  header: ReactNode
  /** Cell renderer. */
  cell: (row: T) => ReactNode
  /** Value used for sorting; omit to make the column unsortable. */
  sortValue?: (row: T) => string | number
  className?: string
  headerClassName?: string
}

export interface DataTableProps<T> {
  columns: Column<T>[]
  rows: T[]
  rowKey: (row: T) => string
  caption: string
  /** Renders instead of the table body when there are no rows. */
  emptyState?: ReactNode
  onRowClick?: (row: T) => void
  className?: string
}

/**
 * A real `<table>`.
 *
 * A table of clinical records is scanned, not skimmed, and screen readers need
 * real headers and row counts — so this is semantic markup with a caption and
 * a scope on every header, wrapped in a horizontally scrollable region for
 * narrow viewports.
 */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  caption,
  emptyState,
  onRowClick,
  className,
}: DataTableProps<T>) {
  if (rows.length === 0 && emptyState) {
    return <div className={className}>{emptyState}</div>
  }

  return (
    <div className={cn('scrollbar-thin overflow-x-auto', className)}>
      <table className="w-full min-w-[36rem] border-collapse text-left text-sm">
        <caption className="sr-only">
          {caption} — {rows.length} {rows.length === 1 ? 'row' : 'rows'}
        </caption>
        <thead>
          <tr className="border-b border-line">
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className={cn(
                  'whitespace-nowrap px-3 py-2.5 text-[12px] font-semibold uppercase tracking-wide text-ink-400 first:pl-0 last:pr-0',
                  column.headerClassName,
                  column.className,
                )}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={rowKey(row)}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              tabIndex={onRowClick ? 0 : undefined}
              onKeyDown={
                onRowClick
                  ? (event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault()
                        onRowClick(row)
                      }
                    }
                  : undefined
              }
              className={cn(
                'border-b border-line/70 last:border-0',
                onRowClick && 'cursor-pointer transition-colors hover:bg-brand-50/50 focus:bg-brand-50/50',
              )}
            >
              {columns.map((column) => (
                <td
                  key={column.key}
                  className={cn('px-3 py-3 align-top text-ink-700 first:pl-0 last:pr-0', column.className)}
                >
                  {column.cell(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
