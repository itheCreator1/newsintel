import type { ReactNode } from 'react'

/** Eyebrow + title header shared by every redesigned page; `children` holds trailing header content (a filter, a link) some pages need. */
export function PageHeader({ eyebrow, title, description, children }: { eyebrow: string; title: string; description?: string; children?: ReactNode }) {
  return (
    <header className="flex flex-wrap items-center justify-between gap-5">
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-primary/80">{eyebrow}</p>
        <h2 className="mt-1.5 font-sans text-[32px] font-bold tracking-tight text-foreground">{title}</h2>
        {description && <p className="mt-1.5 text-sm text-muted-foreground">{description}</p>}
      </div>
      {children}
    </header>
  )
}
