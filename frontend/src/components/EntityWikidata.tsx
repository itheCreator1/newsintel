'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Link from 'next/link'
import { useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { WikidataCandidate, WikidataItemName, WikidataLink } from '../lib/api-types'
import { entityHref } from '../lib/investigation'
import { fieldClass, ghostButtonClass, labelClass } from '../lib/ui-classes'
import { identifierLinks, itemText, qidHref, reasonText, typedQid } from '../lib/wikidata'

// While a search or a fetch waits for its turn at Wikidata, the page asks the API (never Wikidata) again.
const POLL_MS = 3000
const muted = 'text-sm text-muted-foreground'

/** The refusal in words, and the entity already linked to the item when there is one. */
function Refusal({ error }: { error: unknown }) {
  const detail = error instanceof ApiError && error.detail && typeof error.detail === 'object' ? error.detail as { entity_id?: string | null; display_name?: string | null } : {}
  return (
    <p role="alert" className="error text-sm text-destructive">
      {error instanceof ApiError ? error.message : 'Could not change the Wikidata link.'}
      {detail.entity_id && detail.display_name && <>{' '}<Link className="underline" href={entityHref(detail.entity_id)}>{`Open ${detail.display_name}`}</Link></>}
    </p>
  )
}

function ItemLink({ qid }: { qid: string }) {
  return <a className="font-mono text-foreground hover:text-primary" href={qidHref(qid)} target="_blank" rel="noreferrer">{qid}</a>
}

export function Attribution() {
  return <p className="text-xs text-muted-foreground">Data from <a className="underline" href="https://www.wikidata.org/wiki/Wikidata:Licensing" target="_blank" rel="noreferrer">Wikidata</a> (CC0)</p>
}

function Candidates({ items, pending, onLink, onDismiss }: { items: WikidataCandidate[]; pending: boolean; onLink(qid: string): void; onDismiss(qid: string): void }) {
  return (
    <ul aria-label="Wikidata suggestions" className="list-none pl-0 flex flex-col gap-2">
      {items.map(item => (
        <li key={item.qid} className="flex flex-col gap-1 rounded-lg border border-border p-3">
          <span className="flex flex-wrap items-center gap-2 text-sm text-foreground">
            <ItemLink qid={item.qid} />
            {item.label && <span className="font-medium">{item.label}</span>}
          </span>
          {item.description && <span className={muted}>{item.description}</span>}
          <small className="text-xs text-muted-foreground">{item.reasons.map(reasonText).join(' · ')}</small>
          <div className="flex flex-wrap gap-2">
            <button type="button" className={ghostButtonClass} disabled={pending} aria-label={`Link to ${item.qid}`} onClick={() => onLink(item.qid)}>Link</button>
            <button type="button" className={ghostButtonClass} disabled={pending} aria-label={`Not this one: ${item.qid}`} onClick={() => onDismiss(item.qid)}>Not this one</button>
          </div>
        </li>
      ))}
    </ul>
  )
}

/** The item's names this entity lacks: ticked ones are added; a name of another entity links to it. */
function OfferedNames({ names, pending, onAdd }: { names: WikidataItemName[]; pending: boolean; onAdd(names: WikidataItemName[]): void }) {
  const [ticked, setTicked] = useState<Set<string>>(new Set())
  const key = (name: WikidataItemName) => `${name.language}\u0000${name.text}`
  const offered = names.filter(name => name.status !== 'this_entity')
  if (!offered.length) return null
  const toggle = (name: WikidataItemName) => setTicked(current => {
    const next = new Set(current)
    if (next.has(key(name))) next.delete(key(name)); else next.add(key(name))
    return next
  })
  return (
    <div className="flex flex-col gap-2">
      <strong className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Names on Wikidata</strong>
      <ul aria-label="Names on Wikidata" className="list-none pl-0 flex flex-col gap-1">
        {offered.map(name => (
          <li key={key(name)} className="text-sm text-foreground">
            {name.status === 'absent'
              ? <label className="flex items-center gap-2">
                  <input type="checkbox" className="h-4 w-4 accent-primary" checked={ticked.has(key(name))} onChange={() => toggle(name)} />
                  {`${name.text} (${name.language}, ${name.kind})`}
                </label>
              : <>{name.entity_id ? <Link className="hover:text-primary" href={entityHref(name.entity_id)}>{`${name.text} (${name.language})`}</Link> : `${name.text} (${name.language})`} is already a name of another entity; merge them on its page if they are the same.</>}
          </li>
        ))}
      </ul>
      <div>
        <button type="button" className={ghostButtonClass} disabled={pending || !ticked.size}
          onClick={() => onAdd(offered.filter(name => ticked.has(key(name))))}>Add selected names</button>
      </div>
    </div>
  )
}

function Linked({ data, language, pending, onRefresh, onUnlink, onAdd }: {
  data: WikidataLink & { qid: string }; language: string; pending: boolean; onRefresh(): void; onUnlink(): void; onAdd(names: WikidataItemName[]): void
}) {
  const item = data.item
  const summary = item?.state === 'ok' ? [itemText(item.labels, language), itemText(item.descriptions, language)].filter(Boolean).join(' · ') : ''
  const ids = identifierLinks(data.identifiers)
  return (
    <div className="flex flex-col gap-3">
      <p className="flex flex-wrap items-center gap-2 text-sm text-foreground">Linked to <ItemLink qid={data.qid} /></p>
      {summary && <p className={muted}>{summary}</p>}
      {data.fetch_pending && <p className={muted}>Fetching the item from Wikidata…</p>}
      {item?.state === 'redirected' && item.redirect_to && (
        <p className="text-sm text-foreground">
          {`Wikidata merged ${data.qid} into ${item.redirect_to}`}
          {data.redirect_holder
            ? <>{', which '}<Link className="underline" href={entityHref(data.redirect_holder.entity_id)}>{data.redirect_holder.display_name}</Link>{' is linked to. If they are the same, merge the two entities.'}</>
            : '; the link moves at the next refresh.'}
        </p>
      )}
      {item?.state === 'missing' && <p className="text-sm text-foreground">{`Wikidata no longer has ${data.qid}. The link is kept; unlink it if it is wrong.`}</p>}
      {ids.length > 0 && (
        <ul aria-label="Identifiers" className="list-none pl-0 flex flex-wrap gap-3">
          {ids.map(id => <li key={id.scheme}><a className="text-sm text-foreground hover:text-primary" href={id.href} target="_blank" rel="noreferrer">{`${id.label} ${id.value}`}</a></li>)}
        </ul>
      )}
      <OfferedNames key={data.qid} names={data.names} pending={pending} onAdd={onAdd} />
      <div className="flex flex-wrap gap-2">
        <button type="button" className={ghostButtonClass} disabled={pending} onClick={onRefresh}>Refresh from Wikidata</button>
        <button type="button" className={ghostButtonClass} disabled={pending} onClick={onUnlink}>Unlink</button>
      </div>
    </div>
  )
}

/** The root's Wikidata link: suggestions and a search while unlinked; the item, its identifiers and names once linked. */
export function EntityWikidata({ entityId, language }: { entityId: string; language: string }) {
  const client = useQueryClient()
  const [typed, setTyped] = useState('')
  const [message, setMessage] = useState('')
  const link = useQuery({
    queryKey: ['entity-wikidata', entityId], queryFn: () => api.wikidata(entityId), retry: false,
    refetchInterval: query => query.state.data?.search_pending || query.state.data?.fetch_pending ? POLL_MS : false,
  })
  const refresh = () => Promise.all(['entity-wikidata', 'entity-history', 'entity-variants', 'entity'].map(key => client.invalidateQueries({ queryKey: [key] })))
  const done = (text = '') => () => { setMessage(text); return refresh() }

  const linkTo = useMutation({ mutationFn: (qid: string) => api.linkWikidata(entityId, qid, []), onSuccess: () => { setTyped(''); return done()() } })
  const dismiss = useMutation({ mutationFn: (qid: string) => api.dismissWikidataCandidate(entityId, qid), onSuccess: done() })
  const search = useMutation({ mutationFn: () => api.searchWikidata(entityId), onSuccess: done() })
  const update = useMutation({ mutationFn: () => api.refreshWikidata(entityId), onSuccess: done('Refresh queued; the item is checked again shortly.') })
  const unlink = useMutation({ mutationFn: () => api.unlinkWikidata(entityId), onSuccess: done() })
  const names = useMutation({
    mutationFn: (chosen: WikidataItemName[]) => api.addWikidataNames(entityId, chosen.map(({ language: lang, text }) => ({ language: lang, text }))),
    onSuccess: done('The names were added; their articles are searchable by them shortly.'),
  })
  const mutations = [linkTo, dismiss, search, update, unlink, names]
  const pending = mutations.some(mutation => mutation.isPending)
  const error = mutations.find(mutation => mutation.isError)?.error
  const data = link.data
  const qid = typedQid(typed)

  return (
    <div className="flex flex-col gap-3">
      {link.isPending && <p className={muted}>Loading the Wikidata link…</p>}
      {link.isError && <p className="error text-sm text-destructive">Could not load the Wikidata link.</p>}
      {data?.qid && (
        <Linked data={{ ...data, qid: data.qid }} language={language} pending={pending}
          onRefresh={() => update.mutate()} onUnlink={() => unlink.mutate()} onAdd={chosen => names.mutate(chosen)} />
      )}
      {data && !data.qid && (
        <>
          {data.candidates.length > 0
            ? <Candidates items={data.candidates} pending={pending} onLink={item => linkTo.mutate(item)} onDismiss={item => dismiss.mutate(item)} />
            : !data.search_pending && <p className={muted}>No Wikidata suggestions yet.</p>}
          {data.search_pending && <p className={muted}>Searching Wikidata…</p>}
          <div>
            <button type="button" className={ghostButtonClass} disabled={pending || data.search_pending} onClick={() => search.mutate()}>Find on Wikidata</button>
          </div>
          <form className="flex flex-wrap items-end gap-2" onSubmit={event => { event.preventDefault(); if (qid) linkTo.mutate(qid) }}>
            <label className={labelClass}>Wikidata item id
              <input className={fieldClass} placeholder="Q42" maxLength={16} value={typed} onChange={event => setTyped(event.target.value)} />
            </label>
            <button type="submit" className={ghostButtonClass} disabled={pending || !qid}>Link item</button>
          </form>
        </>
      )}
      {message && <p role="status" className="text-sm text-primary">{message}</p>}
      {error && <Refusal error={error} />}
      {data && (data.qid || data.candidates.length > 0) && <Attribution />}
    </div>
  )
}
