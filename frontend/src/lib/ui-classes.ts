/** Shared Tailwind class strings for the redesigned pages — plain constants, not components, so
 * native form elements (<select>, <input>, <label>) keep their exact tag shape for Playwright's
 * implicit-label and role queries. */

export const fieldClass = 'w-full rounded-lg border border-border bg-background/60 px-3 py-2 text-sm text-foreground outline-none focus:border-ring focus:ring-2 focus:ring-ring/40'
export const labelClass = 'flex flex-col gap-1.5 text-xs font-medium text-muted-foreground'
export const ghostButtonClass = 'rounded-lg border border-border bg-transparent px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:border-ring hover:text-foreground disabled:pointer-events-none disabled:opacity-50'
export const chipClass = 'annotation-link rounded-full border border-border px-2.5 py-1 text-xs text-muted-foreground no-underline transition-colors hover:border-ring hover:bg-accent hover:text-accent-foreground'
export const primaryButtonClass = 'rounded-lg bg-primary px-5 py-2.5 text-sm font-semibold text-primary-foreground shadow-glow-sm transition-shadow hover:shadow-glow disabled:pointer-events-none disabled:opacity-50'
