'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Link from 'next/link'
import { useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { SeeAlsoItem, SeeAlsoLabel } from '../lib/api-types'
import { entityHref, toHref } from '../lib/investigation'
import { SEE_ALSO_LABELS } from '../lib/see-also'
import { fieldClass, ghostButtonClass, labelClass } from '../lib/ui-classes'
import { EntityPicker, type Picked } from './EntityAuthority'

const LABEL_TEXT = SEE_ALSO_LABELS
const ORDER = Object.keys(LABEL_TEXT) as SeeAlsoLabel[]

const failure = (error: unknown, fallback: string) => error instanceof ApiError ? error.message : fallback

function period(link: SeeAlsoItem): string {
  if (link.valid_from && link.valid_to) return ` (${link.valid_from} – ${link.valid_to})`
  if (link.valid_from) return ` (from ${link.valid_from})`
  if (link.valid_to) return ` (until ${link.valid_to})`
  return ''
}

interface Details { from: string; until: string; note: string }

function DetailsFields({ details, onChange }: { details: Details; onChange(details: Details): void }) {
  return (
    <>
      <div className="flex flex-wrap gap-3">
        <label className={labelClass}>From
          <input className={fieldClass} placeholder="2009, 2021-10 or 2021-10-28" maxLength={10} value={details.from} onChange={event => onChange({ ...details, from: event.target.value })} />
        </label>
        <label className={labelClass}>Until
          <input className={fieldClass} placeholder="Leave empty if it still holds" maxLength={10} value={details.until} onChange={event => onChange({ ...details, until: event.target.value })} />
        </label>
      </div>
      <label className={labelClass}>Link note
        <textarea className={fieldClass} rows={2} maxLength={4000} value={details.note} onChange={event => onChange({ ...details, note: event.target.value })} />
      </label>
    </>
  )
}

/** Links the user stated between this entity and others: later names, parts, members, leaders. */
export function SeeAlso({ entityId }: { entityId: string }) {
  const client = useQueryClient()
  const links = useQuery({ queryKey: ['entity-see-also', entityId], queryFn: () => api.seeAlso(entityId), retry: false })
  const [editing, setEditing] = useState<string | null>(null)
  const [label, setLabel] = useState<SeeAlsoLabel | ''>('')
  const [target, setTarget] = useState<Picked | null>(null)
  const [details, setDetails] = useState<Details>({ from: '', until: '', note: '' })
  const refresh = () => Promise.all(['entity-see-also', 'entity-history'].map(key => client.invalidateQueries({ queryKey: [key] })))
  const close = () => { setEditing(null); setTarget(null) }

  const add = useMutation({
    mutationFn: () => api.addRelation(entityId, {
      label: (label || links.data!.labels[0])!, target_id: target!.id,
      ...(details.from.trim() ? { valid_from: details.from.trim() } : {}),
      ...(details.until.trim() ? { valid_to: details.until.trim() } : {}),
      ...(details.note.trim() ? { note: details.note.trim() } : {}),
    }),
    onSuccess: () => { close(); return refresh() },
  })
  const update = useMutation({
    mutationFn: (id: string) => api.updateRelation(id, { valid_from: details.from.trim() || null, valid_to: details.until.trim() || null, note: details.note.trim() || null }),
    onSuccess: () => { close(); return refresh() },
  })
  const remove = useMutation({ mutationFn: (link: SeeAlsoItem) => api.removeRelation(link.id), onSuccess: refresh })
  const error = [add, update, remove].find(mutation => mutation.isError)?.error

  function open(link: SeeAlsoItem | null) {
    add.reset(); update.reset()
    setEditing(link ? link.id : 'new')
    setTarget(null)
    setLabel('')
    setDetails({ from: link?.valid_from ?? '', until: link?.valid_to ?? '', note: link?.note ?? '' })
  }

  const items = [...(links.data?.items ?? [])].sort((a, b) =>
    ORDER.indexOf(a.label) - ORDER.indexOf(b.label) || a.entity.display_name.localeCompare(b.entity.display_name) || (a.valid_from ?? '').localeCompare(b.valid_from ?? ''))
  const pending = add.isPending || update.isPending

  return (
    <div className="flex flex-col gap-3">
      {links.isError && <p className="error text-sm text-destructive">Could not load the see-also links.</p>}
      {links.data && !items.length && <p className="text-sm text-muted-foreground">No see-also links yet.</p>}
      {items.length > 0 && (
        <ul aria-label="See also" className="list-none pl-0 flex flex-col gap-1">
          {items.map(link => (
            <li key={link.id} className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              <span>
                {`${LABEL_TEXT[link.label]}: `}
                <Link className="font-medium text-foreground hover:text-primary" href={entityHref(link.entity.id)}>{link.entity.display_name}</Link>
                {period(link)}
                {link.note && ` · ${link.note}`}
                {link.source_article && <>{' · Source: '}<Link className="hover:underline" href={toHref('/articles', new URLSearchParams({ article: link.source_article.id }))}>{link.source_article.title}</Link></>}
              </span>
              <button type="button" className={ghostButtonClass} aria-label={`Edit link to ${link.entity.display_name}`} onClick={() => open(link)}>Edit</button>
              <button type="button" className={ghostButtonClass} aria-label={`Remove link to ${link.entity.display_name}`} disabled={remove.isPending} onClick={() => remove.mutate(link)}>Remove</button>
            </li>
          ))}
        </ul>
      )}

      {editing === null && links.data && <div><button type="button" className={ghostButtonClass} onClick={() => open(null)}>Add link</button></div>}
      {editing !== null && links.data && (
        <form className="flex flex-col gap-3 rounded-lg border border-border p-3" onSubmit={event => { event.preventDefault(); if (editing === 'new') add.mutate(); else update.mutate(editing) }}>
          {editing === 'new' && (
            <>
              <label className={labelClass}>Link type
                <select className={fieldClass} value={label || links.data.labels[0]} onChange={event => setLabel(event.target.value as SeeAlsoLabel)}>
                  {links.data.labels.map(option => <option key={option} value={option}>{LABEL_TEXT[option]}</option>)}
                </select>
              </label>
              <EntityPicker label="Find the linked entity" exclude={entityId} onPick={setTarget} />
              {target && <p className="text-sm text-foreground">{`Linked entity: ${target.text}`}</p>}
            </>
          )}
          <DetailsFields details={details} onChange={setDetails} />
          <div className="flex gap-2">
            <button type="submit" className={ghostButtonClass} disabled={pending || (editing === 'new' && !target)}>Save link</button>
            <button type="button" className={ghostButtonClass} onClick={close}>Cancel</button>
          </div>
        </form>
      )}
      {error && <p role="alert" className="error text-sm text-destructive">{failure(error, 'Could not change this link.')}</p>}
    </div>
  )
}
