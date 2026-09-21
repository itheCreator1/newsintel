import type { FilterChip } from '../lib/filter-ui'
import { chipClass, ghostButtonClass } from '../lib/ui-classes'
import { cn } from '../lib/utils'

/** Applied (URL) criteria as removable chips; renders nothing when no criteria are applied. */
export function ActiveFilterBar({ items, onRemove, onClear, draftDiffers = false }: { items: FilterChip[]; onRemove: (key: string) => void; onClear: () => void; draftDiffers?: boolean }) {
  if (!items.length) return null
  return (
    <section aria-label="Applied filters" className="flex flex-col gap-2">
      <ul className="flex flex-wrap items-center gap-2">
        {items.map(item => (
          <li key={item.key}>
            <button type="button" className={cn(chipClass, 'w-auto bg-transparent font-normal')} aria-label={`Remove ${item.label}`} onClick={() => onRemove(item.key)}>
              <span className="break-all">{item.label}</span> <span aria-hidden="true">×</span>
            </button>
          </li>
        ))}
        <li><button type="button" className={cn(ghostButtonClass, 'w-auto')} onClick={onClear}>Clear all filters</button></li>
      </ul>
      {draftDiffers && <p className="text-xs text-muted-foreground">You have unapplied changes. Filter-chip actions discard them.</p>}
    </section>
  )
}
