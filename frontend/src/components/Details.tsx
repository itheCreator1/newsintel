import type { ReactNode } from 'react'
import { cn } from '../lib/utils'

/** Technical lines (versions, generations, maintenance actions) folded away from the reading flow, closed by default. */
export function Details({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <details className={cn('group rounded-lg border border-border/60 px-3 py-2 text-sm', className)}>
      <summary className="cursor-pointer text-xs font-medium text-muted-foreground hover:text-foreground">Details</summary>
      <div className="mt-2 flex flex-col gap-2">{children}</div>
    </details>
  )
}
