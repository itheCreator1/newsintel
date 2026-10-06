import { cn } from '../lib/utils'
import { labelClass } from '../lib/ui-classes'

export interface PickerOption { value: string; label: string }

/**
 * Multi-value filter as a checkbox list: no Ctrl-click, and it works with touch. Selected values that
 * aren't in the current options (another search term, a value from the URL) stay listed first, so
 * narrowing the list never hides or drops a selection.
 */
export function CheckboxPicker({ legend, options, selected, labels, onChange, emptyText = 'Nothing to pick yet.', className }: {
  legend: string
  options: PickerOption[]
  selected: string[]
  labels?: Map<string, string>
  onChange: (values: string[]) => void
  emptyText?: string
  className?: string
}) {
  const offered = new Set(options.map(option => option.value))
  const items = [
    ...selected.filter(value => !offered.has(value)).map(value => ({ value, label: labels?.get(value) ?? value })),
    ...options,
  ]
  const toggle = (value: string, checked: boolean) => onChange(checked ? [...selected, value] : selected.filter(item => item !== value))
  return (
    <fieldset className={cn(labelClass, 'm-0 min-w-0 border-0 p-0', className)}>
      <legend className="mb-1.5 p-0">{selected.length > 0 ? `${legend} (${selected.length})` : legend}</legend>
      <div className="flex max-h-44 flex-col gap-0.5 overflow-y-auto rounded-lg border border-border bg-background/60 p-1.5">
        {items.length === 0 && <p className="px-1.5 py-1 text-xs text-muted-foreground">{emptyText}</p>}
        {items.map(item => (
          <label key={item.value} className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-sm font-normal text-foreground hover:bg-accent/60">
            <input type="checkbox" className="h-4 w-4 shrink-0 accent-primary" checked={selected.includes(item.value)} onChange={event => toggle(item.value, event.target.checked)} />
            <span className="truncate">{item.label}</span>
          </label>
        ))}
      </div>
    </fieldset>
  )
}
