'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Link from 'next/link'
import { useEffect, useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { EntityClaimResult, EntityDossier, EntityType } from '../lib/api-types'
import { ENTITY_TYPES, typeLabel } from '../lib/authorities'
import { entityHref } from '../lib/investigation'
import { cn, plural } from '../lib/utils'
import { fieldClass, ghostButtonClass, labelClass } from '../lib/ui-classes'
import { EntityAuthority, refreshAuthority } from './EntityAuthority'
import { EntityWikidata } from './EntityWikidata'
import { SeeAlso } from './SeeAlso'

const failure = (error: unknown, fallback: string) => error instanceof ApiError ? error.message : fallback
const sectionTitle = 'text-xs font-semibold uppercase tracking-wide text-muted-foreground'
/** "a Person", "an Organization". */
const withArticle = (type: string) => `${/^[AEIOU]/.test(typeLabel(type)) ? 'an' : 'a'} ${typeLabel(type)}`

/** The type the user gives the root, what the articles called it, and the type Wikidata gives. */
function TypeEditor({ entity }: { entity: EntityDossier }) {
  const client = useQueryClient()
  const [message, setMessage] = useState('')
  const wikidata = useQuery({ queryKey: ['entity-wikidata', entity.id], queryFn: () => api.wikidata(entity.id), retry: false })
  const change = useMutation({
    mutationFn: (type: EntityType) => api.updateEntity(entity.id, { entity_type: type }),
    onMutate: () => setMessage(''),
    onSuccess: (_result, type) => { setMessage(`${entity.display_name} is now ${withArticle(type)}; search and the graph follow shortly.`); return refreshAuthority(client) },
  })
  const offered = wikidata.data?.qid && wikidata.data.wikidata_type && wikidata.data.wikidata_type !== entity.entity_type ? wikidata.data.wikidata_type : null

  return (
    <div className="flex flex-col gap-2">
      <label className={labelClass}>Type
        <select className={fieldClass} value={entity.entity_type} disabled={change.isPending} onChange={event => change.mutate(event.target.value as EntityType)}>
          {ENTITY_TYPES.map(type => <option key={type} value={type}>{typeLabel(type)}</option>)}
        </select>
      </label>
      {entity.ner_type !== entity.entity_type && <small className="text-xs text-muted-foreground">{`The articles call it ${typeLabel(entity.ner_type)}.`}</small>}
      {offered && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border p-2 text-sm text-foreground">
          <span>{`Wikidata ${wikidata.data!.qid} says this is ${withArticle(offered)}.`}</span>
          <button type="button" className={ghostButtonClass} disabled={change.isPending} onClick={() => change.mutate(offered as EntityType)}>{`Change type to ${typeLabel(offered)}`}</button>
        </div>
      )}
      {message && <p role="status" className="text-sm text-primary">{message}</p>}
      {change.isError && <p role="alert" className="error text-sm text-destructive">{failure(change.error, 'Could not change the type.')}</p>}
    </div>
  )
}

function claimed(label: string, result: EntityClaimResult): string {
  const count = result.runs.length
  if (!count) return `Claimed “${label}”: every “${label}” found later joins too.`
  return `Claimed “${label}”: ${plural(count, 'name')} ${count === 1 ? 'joins' : 'join'} shortly, and every “${label}” found later joins too.`
}

/** The root's names spelled the same under other types, and the names it claims for every type. */
function SameName({ entity }: { entity: EntityDossier }) {
  const client = useQueryClient()
  const [message, setMessage] = useState('')
  // The words to offer, the root's own first; each shown as the articles spell it.
  const others = entity.other_types ?? []
  const claims = entity.claims ?? []
  const labels = new Map<string, string>([[entity.normalized_text, entity.display_name]])
  for (const other of others) if (!labels.has(other.normalized_text)) labels.set(other.normalized_text, other.display_name)
  const claim = useMutation({
    mutationFn: (key: string) => api.claimName(entity.id, key),
    onMutate: () => setMessage(''),
    onSuccess: (result, key) => { setMessage(claimed(labels.get(key) ?? key, result)); return refreshAuthority(client) },
  })
  const release = useMutation({
    mutationFn: (key: string) => api.releaseClaim(entity.id, key),
    onMutate: () => setMessage(''),
    onSuccess: (_result, key) => { setMessage(`“${key}” is no longer claimed; names already here stay.`); return refreshAuthority(client) },
  })
  const error = [claim, release].find(mutation => mutation.isError)?.error
  const offers = [...labels].filter(([key]) => !claims.includes(key))
  const pending = claim.isPending || release.isPending

  return (
    <section className="flex flex-col gap-2">
      <h3 className={sectionTitle}>Same name, other types</h3>
      <p className="text-xs text-muted-foreground">Bring every type here: names spelled this way under any type join this entity, now and whenever the articles find them later.</p>
      {others.length > 0 && (
        <ul aria-label="Same name, other types" className="list-none pl-0 flex flex-col gap-1">
          {others.map(other => (
            <li key={other.id} className="text-sm text-muted-foreground">
              <Link className="font-medium text-foreground hover:text-primary" href={entityHref(other.id)}>{other.display_name}</Link>{` · ${typeLabel(other.entity_type)}`}
            </li>
          ))}
        </ul>
      )}
      {offers.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {offers.map(([key, label]) => (
            <button key={key} type="button" className={ghostButtonClass} disabled={pending} onClick={() => claim.mutate(key)}>{`Bring every type of “${label}” here`}</button>
          ))}
        </div>
      )}
      {claims.length > 0 && (
        <ul aria-label="Claimed names" className="list-none pl-0 flex flex-col gap-1">
          {claims.map(key => (
            <li key={key} className="flex flex-wrap items-center gap-2 text-sm text-foreground">
              <span>{`“${key}”: every type joins here`}</span>
              <button type="button" className={ghostButtonClass} disabled={pending} aria-label={`Stop claiming “${key}”`} onClick={() => release.mutate(key)}>Stop</button>
            </li>
          ))}
        </ul>
      )}
      {message && <p role="status" className="text-sm text-primary">{message}</p>}
      {claim.data && claim.data.skipped.length > 0 && (
        <ul aria-label="Not brought here" className="list-none pl-0 flex flex-col gap-1 text-sm text-muted-foreground">
          {claim.data.skipped.map(item => <li key={item.entity_id}>{`${item.display_name} (${typeLabel(item.entity_type)}): ${item.reason}`}</li>)}
        </ul>
      )}
      {error && <p role="alert" className="error text-sm text-destructive">{failure(error, 'Could not change the claim.')}</p>}
    </section>
  )
}

/** "Not a name": hides the root from search, the graph and the suggestions without deleting anything. */
function Visibility({ entity }: { entity: EntityDossier }) {
  const client = useQueryClient()
  const change = useMutation({ mutationFn: (hidden: boolean) => api.updateEntity(entity.id, { hidden }), onSuccess: () => refreshAuthority(client) })
  return (
    <div className="flex flex-col gap-2">
      {entity.hidden
        ? <p className="text-sm text-foreground">Hidden: left out of search, the graph and the suggestions.</p>
        : <small className="text-xs text-muted-foreground">For a word the articles took for a name, such as a weekday. Nothing is deleted.</small>}
      <div>
        <button type="button" className={ghostButtonClass} disabled={change.isPending} onClick={() => change.mutate(!entity.hidden)}>
          {entity.hidden ? 'Show again' : 'Hide as not a name'}
        </button>
      </div>
      {change.isError && <p role="alert" className="error text-sm text-destructive">{failure(change.error, 'Could not change this entity.')}</p>}
    </div>
  )
}

/**
 * One root's authority entry, editable in place: type, names, status, claims, merges, see-also and
 * Wikidata. The authority file opens it beside its table; the dossier opens it over the page.
 */
export function AuthorityPanel({ entityId, entity: given, onDossier = false, onClose, onOpen, className }: {
  entityId: string
  /** The dossier already loaded by the page, so the panel does not ask again. */
  entity?: EntityDossier
  /** Opened from the dossier itself: no link back, and see-also and Wikidata stay on the page. */
  onDossier?: boolean
  onClose?: () => void
  /** Opens another root in the panel, as after a merge; without it the merge opens the kept root's dossier. */
  onOpen?: (id: string) => void
  className?: string
}) {
  const dossier = useQuery({ queryKey: ['entity', entityId, 30], queryFn: () => api.entityDossier(entityId, 30), enabled: !given, retry: false })
  const entity = given ?? dossier.data

  useEffect(() => {
    if (!onClose) return
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    document.addEventListener('keydown', close)
    return () => document.removeEventListener('keydown', close)
  }, [onClose])

  return (
    <aside aria-label={entity ? `Edit ${entity.display_name}` : 'Edit entity'}
      className={cn('fixed inset-y-0 right-0 z-40 flex w-full max-w-md flex-col gap-5 overflow-y-auto border-l border-border bg-background p-5 font-sans shadow-2xl', className)}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <h2 className="font-sans text-lg font-semibold tracking-normal text-foreground">{entity?.display_name ?? 'Loading…'}</h2>
          {entity && <small className="text-xs text-muted-foreground">{`${typeLabel(entity.entity_type)} · ${entity.language} · ${plural(entity.article_count, 'article')}`}</small>}
          {entity && !onDossier && <Link className="text-sm text-primary hover:underline" href={entityHref(entity.id)}>Open dossier</Link>}
        </div>
        {onClose && <button type="button" className={ghostButtonClass} aria-label="Close the editor" onClick={onClose}>Close</button>}
      </div>
      {!given && dossier.isError && <p className="error text-sm text-destructive">{failure(dossier.error, 'Could not load this entity.')}</p>}
      {entity && (
        <>
          <TypeEditor key={`type-${entity.id}`} entity={entity} />
          <EntityAuthority key={entity.id} entity={entity} onMerged={onOpen} />
          <SameName key={`same-${entity.id}`} entity={entity} />
          <Visibility entity={entity} />
          {!onDossier && (
            <>
              <section className="flex flex-col gap-2">
                <h3 className={sectionTitle}>See also</h3>
                <SeeAlso key={entity.id} entityId={entity.id} />
              </section>
              <section className="flex flex-col gap-2">
                <h3 className={sectionTitle}>Wikidata</h3>
                <EntityWikidata key={entity.id} entityId={entity.id} language={entity.language} />
              </section>
            </>
          )}
        </>
      )}
    </aside>
  )
}
