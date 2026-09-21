import type { ReactNode } from 'react'
import { ghostButtonClass } from '../lib/ui-classes'
import { cn } from '../lib/utils'

/** Initial-load placeholder: one announced label, with skeleton geometry hidden from assistive tech. */
export function LoadingState({ label, variant = 'rows' }: { label: string; variant?: 'rows' | 'chart' }) {
  return (
    <div role="status" className="flex flex-col gap-3">
      <span className="sr-only">{label}</span>
      <div aria-hidden="true" className="flex flex-col gap-3">
        {variant === 'chart'
          ? <div className="h-[220px] rounded-xl bg-muted" />
          : [0, 1, 2].map(row => <div key={row} className="h-4 rounded bg-muted" style={{ width: `${90 - row * 20}%` }} />)}
      </div>
    </div>
  )
}

export function EmptyState({ title, description, action }: { title: string; description?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-start gap-2 text-sm">
      <p className="font-medium text-foreground">{title}</p>
      {description && <p className="text-muted-foreground">{description}</p>}
      {action}
    </div>
  )
}

/** Presentation only: the page decides which failures are transient enough to offer Retry. */
export function ErrorNotice({ message, onRetry, retrying = false }: { message: string; onRetry?: () => void; retrying?: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <p role="alert" className="error text-sm text-destructive">{message}</p>
      {onRetry && <button type="button" className={cn(ghostButtonClass, 'w-auto')} disabled={retrying} onClick={onRetry}>{retrying ? 'Retrying…' : 'Retry'}</button>}
    </div>
  )
}
