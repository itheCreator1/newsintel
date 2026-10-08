'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRouter } from 'next/navigation'
import { useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { EntityAuthorityUpdate, EntityDossier, EntityHistory } from '../lib/api-types'
import { entityHref } from '../lib/investigation'
import { chipClass, fieldClass, ghostButtonClass, labelClass } from '../lib/ui-classes'

type Change = EntityHistory['items'][number]
interface Picked { id: string; text: string }

const failure = (error: unknown, fallback: string) => error instanceof ApiError ? error.message : fallback

function describe(change: Change): string {
  const after = change.after ?? {}
  switch (change.action) {
    case 'merged': return 'Merged a name into this entity'
    case 'split': return 'Split a name back out'
    case 'renamed': return after.preferred_text ? `Renamed to ${String(after.preferred_text)}` : 'Name reset to the spelling in the articles'
    case 'status_changed': return `Marked ${String(after.status)}`
    case 'ambiguous_changed': return after.ambiguous ? 'Marked as an ambiguous name' : 'No longer marked as ambiguous'
    case 'distinct_added': return 'Recorded a different entity'
    case 'distinct_removed': return 'Removed a different-entity record'
    default: return change.action
  }
}

/** Finds another entity by any of its names; the picker lists roots only. */
function EntityPicker({ label, exclude, onPick }: { label: string; exclude: string; onPick(entity: Picked): void }) {
  const [text, setText] = useState('')
  const term = text.trim()
  const lookup = useQuery({ queryKey: ['authority-entities', term], queryFn: () => api.nlpEntities(term), enabled: term.length >= 2, retry: false })
  const items = (lookup.data?.items ?? []).filter(item => item.id !== exclude)
  return (
    <div className="flex flex-col gap-2">
      <label className={labelClass}>{label}
        <input className={fieldClass} placeholder="Type at least two letters" value={text} onChange={event => setText(event.target.value)} />
      </label>
      {lookup.isError && <p className="error text-sm text-destructive">Could not search entities.</p>}
      {lookup.data && !items.length && <p className="text-sm text-muted-foreground">No entities match.</p>}
      <div className="flex flex-wrap gap-2">
        {items.map(item => <button key={item.id} type="button" className={chipClass} onClick={() => onPick({ id: item.id, text: item.text })}>{`${item.text} (${item.kind})`}</button>)}
      </div>
    </div>
  )
}

/** Pick an entity, then confirm the action on it; used for "merge with" and "not the same as". */
function PickAndConfirm({ open, search, verb, exclude, pending, onConfirm }: {
  open: string; search: string; verb(entity: Picked): string; exclude: string; pending: boolean; onConfirm(entity: Picked): void
}) {
  const [shown, setShown] = useState(false)
  const [picked, setPicked] = useState<Picked | null>(null)
  if (!shown) return <button type="button" className={ghostButtonClass} onClick={() => setShown(true)}>{open}</button>
  return (
    <div className="flex w-full flex-col gap-2 rounded-lg border border-border p-3">
      <EntityPicker label={search} exclude={exclude} onPick={setPicked} />
      {picked && <button type="button" className={ghostButtonClass} disabled={pending} onClick={() => onConfirm(picked)}>{verb(picked)}</button>}
      <button type="button" className={ghostButtonClass} onClick={() => { setShown(false); setPicked(null) }}>Cancel</button>
    </div>
  )
}

/** The authority file entry of one root: its names, status, note, history and the merge/split controls. */
export function EntityAuthority({ entity }: { entity: EntityDossier }) {
  const client = useQueryClient()
  const router = useRouter()
  const rootId = entity.id
  const [renaming, setRenaming] = useState(false)
  const [name, setName] = useState(entity.preferred_text ?? entity.display_name)
  const [note, setNote] = useState(entity.note ?? '')
  const [message, setMessage] = useState('')

  const variants = useQuery({ queryKey: ['entity-variants', rootId], queryFn: () => api.entityVariants(rootId), retry: false })
  const history = useQuery({ queryKey: ['entity-history', rootId], queryFn: () => api.entityHistory(rootId), retry: false })
  const refresh = () => Promise.all(['entity', 'entity-variants', 'entity-history'].map(key => client.invalidateQueries({ queryKey: [key] })))

  const update = useMutation({
    mutationFn: (changes: EntityAuthorityUpdate) => api.updateEntity(rootId, changes),
    onSuccess: () => { setRenaming(false); return refresh() },
  })
  const merge = useMutation({
    mutationFn: (target: Picked) => api.mergeEntity(rootId, target.id),
    onSuccess: run => { void refresh(); router.push(entityHref(run.root_id)) },
  })
  const split = useMutation({
    mutationFn: (variant: { id: string; display_name: string }) => api.splitEntity(variant.id),
    onSuccess: (_run, variant) => { setMessage(`${variant.display_name} is its own entity again; its articles move back shortly.`); return refresh() },
  })
  const distinct = useMutation({
    mutationFn: (other: Picked) => api.addDistinct(rootId, other.id),
    onSuccess: (_result, other) => { setMessage(`${other.text} is recorded as a different entity.`); return refresh() },
  })
  const error = [update, merge, split, distinct].find(mutation => mutation.isError)?.error
  const established = entity.status === 'established'

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className={chipClass}>{established ? 'Established' : 'Provisional'}</span>
        <button type="button" className={ghostButtonClass} disabled={update.isPending} onClick={() => update.mutate({ status: established ? 'provisional' : 'established' })}>
          {established ? 'Mark provisional' : 'Mark established'}
        </button>
        {!renaming && <button type="button" className={ghostButtonClass} onClick={() => setRenaming(true)}>Rename</button>}
        {entity.preferred_text && <button type="button" className={ghostButtonClass} disabled={update.isPending} onClick={() => update.mutate({ preferred_text: null })}>Use the spelling in the articles</button>}
      </div>

      {renaming && (
        <form className="flex flex-wrap items-end gap-2" onSubmit={event => { event.preventDefault(); update.mutate({ preferred_text: name.trim() || null }) }}>
          <label className={labelClass}>Preferred name
            <input className={fieldClass} value={name} maxLength={500} onChange={event => setName(event.target.value)} />
          </label>
          <button type="submit" className={ghostButtonClass} disabled={update.isPending}>Save name</button>
          <button type="button" className={ghostButtonClass} onClick={() => setRenaming(false)}>Cancel</button>
        </form>
      )}

      <label className="flex items-center gap-2 text-sm text-foreground">
        <input type="checkbox" className="h-4 w-4 accent-primary" checked={entity.ambiguous} disabled={update.isPending}
          onChange={event => update.mutate({ ambiguous: event.target.checked })} />
        Ambiguous name (may stand for several people)
      </label>

      <form className="flex flex-col gap-2" onSubmit={event => { event.preventDefault(); update.mutate({ note: note.trim() || null }) }}>
        <label className={labelClass}>Note
          <textarea className={fieldClass} rows={2} maxLength={4000} value={note} onChange={event => setNote(event.target.value)} />
        </label>
        <div><button type="submit" className={ghostButtonClass} disabled={update.isPending}>Save note</button></div>
      </form>

      <div className="flex flex-col gap-2">
        <strong className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Other names</strong>
        {variants.isError && <p className="error text-sm text-destructive">Could not load the other names.</p>}
        {variants.data && !variants.data.items.length && <p className="text-sm text-muted-foreground">No other names merged into this entity.</p>}
        {variants.data && variants.data.items.length > 0 && (
          <ul aria-label="Other names" className="flex flex-col gap-1">
            {variants.data.items.map(variant => (
              <li key={variant.id} className="flex items-center gap-3 text-sm text-foreground">
                <span>{variant.display_name}</span>
                <button type="button" className={ghostButtonClass} aria-label={`Split ${variant.display_name}`} disabled={split.isPending} onClick={() => split.mutate(variant)}>Split</button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="flex flex-wrap items-start gap-3">
        <PickAndConfirm open="Merge with…" search="Find the entity to merge into" exclude={rootId} pending={merge.isPending}
          verb={target => `Merge into ${target.text}`} onConfirm={target => merge.mutate(target)} />
        <PickAndConfirm open="Not the same as…" search="Find the entity that is different" exclude={rootId} pending={distinct.isPending}
          verb={other => `Mark ${other.text} as different`} onConfirm={other => distinct.mutate(other)} />
      </div>

      {message && <p role="status" className="text-sm text-primary">{message}</p>}
      {error && <p role="alert" className="error text-sm text-destructive">{failure(error, 'Could not change this entity.')}</p>}

      {history.data && history.data.items.length > 0 && (
        <div className="flex flex-col gap-2">
          <strong className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">History</strong>
          <ul aria-label="Authority history" className="flex flex-col gap-1">
            {history.data.items.map(change => (
              <li key={change.id} className="text-sm text-muted-foreground">{`${describe(change)} · ${new Date(change.created_at).toLocaleString()}`}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
