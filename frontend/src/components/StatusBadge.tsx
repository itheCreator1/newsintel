import type { ReactNode } from 'react'
import { cn } from '../lib/utils'

/** Small status pill shared by list rows (Sources, Jobs) that show a healthy/pending-style state. */
export function StatusBadge({ tone, children }: { tone: 'healthy' | 'pending'; children: ReactNode }) {
  return (
    <span className={cn(
      'h-fit shrink-0 rounded-full px-2.5 py-1 text-xs font-medium',
      tone === 'healthy' ? 'bg-primary/15 text-primary' : 'bg-white/8 text-muted-foreground',
    )}>
      {children}
    </span>
  )
}
