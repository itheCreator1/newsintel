import type { ComponentProps } from 'react'
import { cn } from '../lib/utils'

/** Exported for elements that can't be a `<section>` (a `<form>`, for instance) but need the same look. */
export const glassPanelClassName = 'rounded-2xl border border-border bg-card p-6 shadow-card'

/** Card panel shared by the redesigned pages (Overview, Search). */
export function GlassPanel({ className, ...rest }: ComponentProps<'section'>) {
  return <section className={cn(glassPanelClassName, className)} {...rest} />
}
