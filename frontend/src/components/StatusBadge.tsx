import type { ReactNode } from 'react'
import { cn } from '../lib/utils'

export type BadgeTone = 'healthy' | 'pending' | 'degraded' | 'error'
const TONES: Record<BadgeTone, string> = {
  healthy: 'bg-primary/15 text-primary',
  pending: 'bg-white/8 text-muted-foreground',
  degraded: 'bg-yellow-500/15 text-yellow-400',
  error: 'bg-destructive/15 text-destructive',
}

/** Small status pill shared by list rows (Sources, Jobs, Operations) that show a state. */
export function StatusBadge({ tone, children }: { tone: BadgeTone; children: ReactNode }) {
  return (
    <span className={cn(
      'h-fit shrink-0 rounded-full px-2.5 py-1 text-xs font-medium',
      TONES[tone],
    )}>
      {children}
    </span>
  )
}
