'use client'

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useEffect, useState, type ReactNode } from 'react'
import { AuthorityPanel } from '../../components/AuthorityPanel'
import { CheckboxPicker } from '../../components/CheckboxPicker'
import { refreshAuthority } from '../../components/EntityAuthority'
import { Attribution } from '../../components/EntityWikidata'
import { LoadError } from '../../components/Feedback'
import { GlassPanel } from '../../components/GlassPanel'
import { PageHeader } from '../../components/PageHeader'
import { api, ApiError } from '../../lib/api'
import type { AuthorityBulk, AuthorityHistoryPage, AuthorityRootPage, AuthoritySuggestionList, EntityType, WikidataReviewItem } from '../../lib/api-types'
import { authoritiesHref, authorityFilters, ENTITY_TYPES, readAuthoritiesQuery, typeLabel, type AuthoritiesView, type AuthorityTab } from '../../lib/authorities'
import { entityHref } from '../../lib/investigation'
import { cn } from '../../lib/utils'
import { chipClass, fieldClass, ghostButtonClass, labelClass } from '../../lib/ui-classes'
import { qidHref, reasonText as wikidataReason, wikidataChange } from '../../lib/wikidata'

type Suggestion = AuthoritySuggestionList['items'][number]
type Change = AuthorityHistoryPage['items'][number]
type Name = Suggestion['root']
type Root = AuthorityRootPage['items'][number]

const plural = (count: number, word: string, many = `${word}s`) => `${count} ${count === 1 ? word : many}`
const failure = (error: unknown, fallback: string) => error instanceof ApiError ? error.message : fallback
const sectionTitle = 'text-xs font-semibold uppercase tracking-wide text-muted-foreground'
const cellClass = 'whitespace-nowrap px-3 py-2 align-middle'

function EntityLink({ id, name }: { id: string | null; name: string | null }) {
  if (!id || !name) return <span>{name ?? 'Unknown entity'}</span>
  return <Link className="font-medium text-foreground hover:text-primary" href={entityHref(id)}>{name}</Link>
}

/** One change in words, naming both entities when it has two. */
function describe(change: Change): ReactNode {
  const before = change.before ?? {}
  const after = change.after ?? {}
  const entity = <EntityLink id={change.entity_id} name={change.entity_name} />
  const other = <EntityLink id={change.other_id} name={change.other_name ?? (change.other_id ? 'another entity' : null)} />
  switch (change.action) {
    case 'merged': return after.by === 'claim' ? <>{entity} joined {other} by its claimed name</> : <>{entity} merged into {other}</>
    case 'split': return <>{entity} split back out of {other}</>
    case 'renamed': return <>{entity}{after.preferred_text ? `: renamed to ${String(after.preferred_text)}` : ': name reset to the spelling in the articles'}</>
    case 'status_changed': return <>{entity}: marked {String(after.status)}</>
    case 'ambiguous_changed': return <>{entity}{after.ambiguous ? ': marked as an ambiguous name' : ': no longer marked as ambiguous'}</>
    case 'distinct_added': return <>{entity} and {other} recorded as different</>
    case 'distinct_removed': return <>{entity} and {other} no longer recorded as different</>
    case 'relation_added': return <>{entity} linked to {other}</>
    case 'relation_changed': return <>{entity}: link to {other} changed</>
    case 'relation_removed': return <>{entity} no longer linked to {other}</>
    case 'type_changed': return <>{entity}{`: type changed from ${typeLabel(String(before.entity_type))} to ${typeLabel(String(after.entity_type))}`}</>
    case 'hidden_changed': return <>{entity}{after.hidden ? ': hidden as not a name' : ': shown again'}</>
    case 'claim_added': return <>{entity}{`: claims every type of “${String(after.normalized_text)}”`}</>
    case 'claim_removed': return <>{entity}{`: no longer claims “${String(before.normalized_text)}”`}</>
    default: {
      const text = wikidataChange(change)
      if (!text) return <>{entity}: {change.action}</>
      return <>{entity}: {text.startsWith('Wikidata') ? text : text.charAt(0).toLowerCase() + text.slice(1)}</>
    }
  }
}

function reasonText(item: Suggestion): string {
  const text = [...item.reasons, ...(item.shared_articles ? [`${plural(item.shared_articles, 'article')} together`] : [])].join(' · ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

type Decision = { kind: 'same' | 'keep-variant' | 'different'; item: Suggestion }

function decide({ kind, item }: Decision): Promise<unknown> {
  if (kind === 'same') return api.mergeEntity(item.variant.id, item.root.id)
  if (kind === 'keep-variant') return api.mergeEntity(item.root.id, item.variant.id)
  return api.addDistinct(item.root.id, item.variant.id)
}

function outcome({ kind, item }: Decision): string {
  const [kept, joined] = kind === 'keep-variant' ? [item.variant, item.root] : [item.root, item.variant]
  if (kind === 'different') return `${item.variant.display_name} and ${item.root.display_name} are recorded as different.`
  return `${joined.display_name} is now a name of ${kept.display_name}; its articles move over shortly.`
}

const suggestionsQuery = (language: string) => ({ queryKey: ['authority-suggestions', language], queryFn: () => api.authoritySuggestions(language || undefined) })
const wikidataQuery = {
  queryKey: ['wikidata-review'],
  initialPageParam: undefined as string | undefined,
  queryFn: ({ pageParam }: { pageParam: string | undefined }) => api.wikidataCandidates(pageParam),
  getNextPageParam: (page: { next_cursor?: string | null }) => page.next_cursor ?? undefined,
}

function SuggestedName({ name }: { name: Name }) {
  return (
    <span className="flex flex-col">
      <EntityLink id={name.id} name={name.display_name} />
      <small className="text-xs text-muted-foreground">{`${typeLabel(name.entity_type)} · ${plural(name.article_count, 'article')}`}</small>
    </span>
  )
}

/** "Maybe the same?": approve merges the less used name into the other, reject records them as different. */
function SuggestionQueue({ language }: { language: string }) {
  const client = useQueryClient()
  const [message, setMessage] = useState('')
  const suggestions = useQuery(suggestionsQuery(language))
  const decision = useMutation({
    mutationFn: decide,
    onMutate: () => setMessage(''),
    onSuccess: (_result, chosen) => { setMessage(outcome(chosen)); return refreshAuthority(client) },
  })
  const items = suggestions.data?.items ?? []

  return (
    <GlassPanel className="flex flex-col gap-4 p-6">
      <p className="text-sm text-muted-foreground">Names that look alike, across types too. Nothing is merged until you decide; the first name keeps its place unless you choose the other.</p>
      {suggestions.isPending && <p className="text-sm text-muted-foreground">Looking for likely duplicates…</p>}
      {suggestions.isError && <LoadError query={suggestions} message="Could not load suggestions." />}
      {suggestions.isSuccess && !items.length && <p className="text-sm text-muted-foreground">No likely duplicates right now.</p>}
      {message && <p role="status" className="text-sm text-primary">{message}</p>}
      {decision.isError && <p role="alert" className="error text-sm text-destructive">{failure(decision.error, 'Could not record this decision.')}</p>}
      {items.length > 0 && (
        <ul aria-label="Maybe the same?" className="list-none pl-0 flex flex-col divide-y divide-border">
          {items.map(item => {
            const { root, variant } = item
            return (
              <li key={`${root.id}-${variant.id}`} className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 py-3">
                <div className="flex min-w-0 flex-col gap-1">
                  <p className="text-sm text-foreground">{`Is “${variant.display_name}” the same as “${root.display_name}”?`}</p>
                  <div className="flex flex-wrap gap-6 text-sm">
                    <SuggestedName name={variant} />
                    <SuggestedName name={root} />
                  </div>
                  <small className="text-xs text-muted-foreground">{reasonText(item)}</small>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button type="button" className={ghostButtonClass} disabled={decision.isPending}
                    aria-label={`Same entity: merge ${variant.display_name} into ${root.display_name}`}
                    onClick={() => decision.mutate({ kind: 'same', item })}>Same entity</button>
                  <button type="button" className={ghostButtonClass} disabled={decision.isPending}
                    aria-label={`Same entity, keep ${variant.display_name}: merge ${root.display_name} into ${variant.display_name}`}
                    onClick={() => decision.mutate({ kind: 'keep-variant', item })}>{`Same, keep “${variant.display_name}”`}</button>
                  <button type="button" className={ghostButtonClass} disabled={decision.isPending}
                    aria-label={`Different: keep ${variant.display_name} apart from ${root.display_name}`}
                    onClick={() => decision.mutate({ kind: 'different', item })}>Different</button>
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </GlassPanel>
  )
}

/** Possible Wikidata items for unlinked names, best first; nothing is linked until the user decides. */
function WikidataReview() {
  const client = useQueryClient()
  const [message, setMessage] = useState('')
  const pages = useInfiniteQuery(wikidataQuery)
  const refresh = () => Promise.all(['wikidata-review', 'authorities', 'authority-history'].map(key => client.invalidateQueries({ queryKey: [key] })))
  const decision = useMutation({
    mutationFn: ({ link, item }: { link: boolean; item: WikidataReviewItem }): Promise<unknown> => link ? api.linkWikidata(item.entity_id, item.qid, []) : api.dismissWikidataCandidate(item.entity_id, item.qid),
    onMutate: () => setMessage(''),
    onSuccess: (_result, { link, item }) => { setMessage(link ? `${item.display_name} is linked to ${item.qid}.` : `${item.display_name} is not ${item.qid}.`); return refresh() },
  })
  const approve = useMutation({ mutationFn: api.approveExactWikidata, onMutate: () => setMessage(''), onSuccess: refresh })
  const items = pages.data?.pages.flatMap(page => page.items) ?? []
  const pending = decision.isPending || approve.isPending
  const error = [decision, approve].find(mutation => mutation.isError)?.error

  return (
    <GlassPanel className="flex flex-col gap-4 p-6">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <p className="text-sm text-muted-foreground">Possible Wikidata items for names not linked yet. Nothing is linked until you decide. Approve all exact links every name whose one exact label has the right type.</p>
        <button type="button" className={ghostButtonClass} disabled={pending || !items.length} onClick={() => approve.mutate()}>Approve all exact</button>
      </div>
      {pages.isPending && <p className="text-sm text-muted-foreground">Loading Wikidata suggestions…</p>}
      {pages.isError && <LoadError query={pages} message="Could not load the Wikidata suggestions." />}
      {pages.isSuccess && !items.length && <p className="text-sm text-muted-foreground">No Wikidata suggestions to review.</p>}
      {message && <p role="status" className="text-sm text-primary">{message}</p>}
      {approve.data && (
        <div role="status" className="flex flex-col gap-1 text-sm text-primary">
          <p>{`Linked ${plural(approve.data.linked, 'entity', 'entities')}; ${approve.data.skipped.length} skipped.`}</p>
          {approve.data.skipped.length > 0 && (
            <ul aria-label="Skipped" className="list-none pl-0 flex flex-col gap-1 text-muted-foreground">
              {approve.data.skipped.map(skip => <li key={`${skip.entity_id}-${skip.qid}`}><EntityLink id={skip.entity_id} name={skip.message} /></li>)}
            </ul>
          )}
        </div>
      )}
      {error && <p role="alert" className="error text-sm text-destructive">{failure(error, 'Could not record this decision.')}</p>}
      {items.length > 0 && (
        <ul aria-label="Wikidata suggestions" className="list-none pl-0 flex flex-col divide-y divide-border">
          {items.map(item => (
            <li key={`${item.entity_id}-${item.qid}`} className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 py-3">
              <div className="flex min-w-0 flex-col gap-1">
                <p className="flex flex-wrap items-center gap-2 text-sm">
                  <EntityLink id={item.entity_id} name={item.display_name} />
                  <a className="font-mono text-foreground hover:text-primary" href={qidHref(item.qid)} target="_blank" rel="noreferrer">{item.qid}</a>
                  <small className="text-xs text-muted-foreground">{`${typeLabel(item.entity_type)} · ${item.language}`}</small>
                </p>
                {(item.label || item.description) && <span className="text-sm text-muted-foreground">{[item.label, item.description].filter(Boolean).join(' · ')}</span>}
                <small className="text-xs text-muted-foreground">{item.reasons.map(wikidataReason).join(' · ')}</small>
              </div>
              <div className="flex flex-wrap gap-2">
                <button type="button" className={ghostButtonClass} disabled={pending} aria-label={`Link ${item.display_name} to ${item.qid}`}
                  onClick={() => decision.mutate({ link: true, item })}>Link</button>
                <button type="button" className={ghostButtonClass} disabled={pending} aria-label={`${item.display_name} is not ${item.qid}`}
                  onClick={() => decision.mutate({ link: false, item })}>Not this one</button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {pages.hasNextPage && (
        <div><button type="button" className={ghostButtonClass} disabled={pages.isFetchingNextPage} onClick={() => pages.fetchNextPage()}>
          {pages.isFetchingNextPage ? 'Loading…' : 'Load more suggestions'}
        </button></div>
      )}
      {items.length > 0 && <Attribution />}
    </GlassPanel>
  )
}

type Picked = Pick<Root, 'id' | 'display_name'>

/** One change to every selected name: merge into the one kept, a type, a status, hide or show. */
function BulkBar({ selected, pending, onApply, onClear }: {
  selected: Picked[]; pending: boolean; onApply(change: AuthorityBulk, kept?: Picked): void; onClear(): void
}) {
  const [type, setType] = useState<EntityType | ''>('')
  const [keptId, setKeptId] = useState(selected[0].id)
  const kept = selected.find(item => item.id === keptId) ?? selected[0]
  const ids = selected.map(item => item.id)
  const others = ids.filter(id => id !== kept.id)
  return (
    <div role="toolbar" aria-label="Bulk actions" className="flex flex-wrap items-end gap-3 rounded-lg border border-ring/60 bg-accent/30 p-3">
      <strong className="self-center text-sm text-foreground">{`${selected.length} selected`}</strong>
      {selected.length > 1 && (
        <div className="flex items-end gap-2">
          <label className={labelClass}>Keep
            <select className={fieldClass} value={kept.id} onChange={event => setKeptId(event.target.value)}>
              {selected.map(item => <option key={item.id} value={item.id}>{item.display_name}</option>)}
            </select>
          </label>
          <button type="button" className={ghostButtonClass} disabled={pending}
            onClick={() => onApply({ action: 'merge', ids: others, target_id: kept.id }, kept)}>{`Merge ${others.length} into ${kept.display_name}`}</button>
        </div>
      )}
      <div className="flex items-end gap-2">
        <label className={labelClass}>Set type to
          <select className={fieldClass} value={type} onChange={event => setType(event.target.value as EntityType | '')}>
            <option value="">Choose…</option>
            {ENTITY_TYPES.map(value => <option key={value} value={value}>{typeLabel(value)}</option>)}
          </select>
        </label>
        <button type="button" className={ghostButtonClass} disabled={pending || !type} onClick={() => type && onApply({ action: 'set_type', ids, entity_type: type })}>Apply type</button>
      </div>
      <button type="button" className={ghostButtonClass} disabled={pending} onClick={() => onApply({ action: 'set_status', ids, status: 'established' })}>Mark established</button>
      <button type="button" className={ghostButtonClass} disabled={pending} onClick={() => onApply({ action: 'set_status', ids, status: 'provisional' })}>Mark provisional</button>
      <button type="button" className={ghostButtonClass} disabled={pending} onClick={() => onApply({ action: 'hide', ids })}>Hide</button>
      <button type="button" className={ghostButtonClass} disabled={pending} onClick={() => onApply({ action: 'show', ids })}>Show</button>
      <button type="button" className={ghostButtonClass} onClick={onClear}>Clear selection</button>
    </div>
  )
}

function done(change: AuthorityBulk, kept?: Picked): string {
  const count = change.ids.length
  if (change.action === 'merge') return `${plural(count, 'name')} ${count === 1 ? 'is' : 'are'} merging into ${kept?.display_name}; ${count === 1 ? 'its' : 'their'} articles move over shortly.`
  return `Changed ${plural(count, 'name')}.`
}

/** The names as a table: tick rows for a bulk change, click a name to edit it beside the table. */
function AuthorityTable({ view, onOpen }: { view: AuthoritiesView; onOpen(id: string): void }) {
  const client = useQueryClient()
  const filters = authorityFilters(view)
  const pages = useInfiniteQuery({
    queryKey: ['authorities', filters],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => api.authorities(filters, pageParam),
    getNextPageParam: page => page.next_cursor ?? undefined,
  })
  const [selected, setSelected] = useState<Picked[]>([])
  const [message, setMessage] = useState('')
  const bulk = useMutation({
    mutationFn: ({ change }: { change: AuthorityBulk; kept?: Picked }) => api.bulkAuthorities(change),
    onMutate: () => setMessage(''),
    onSuccess: (_result, { change, kept }) => { setMessage(done(change, kept)); setSelected([]); return refreshAuthority(client) },
  })
  const items = pages.data?.pages.flatMap(page => page.items) ?? []
  const chosen = new Set(selected.map(item => item.id))
  const allChosen = items.length > 0 && items.every(item => chosen.has(item.id))
  const toggle = (item: Root, on: boolean) => setSelected(current => on ? [...current, { id: item.id, display_name: item.display_name }] : current.filter(other => other.id !== item.id))

  return (
    <>
      {message && <p role="status" className="text-sm text-primary">{message}</p>}
      {bulk.isError && <p role="alert" className="error text-sm text-destructive">{failure(bulk.error, 'Could not change these names.')}</p>}
      {selected.length > 0 && (
        <BulkBar selected={selected} pending={bulk.isPending} onClear={() => setSelected([])} onApply={(change, kept) => bulk.mutate({ change, kept })} />
      )}
      {pages.isPending && <p className="text-sm text-muted-foreground">Loading the authority file…</p>}
      {pages.isError && <LoadError query={pages} message="Could not load the authority file." />}
      {pages.isSuccess && !items.length && <p className="text-sm text-muted-foreground">No names match.</p>}
      {items.length > 0 && (
        <div className="overflow-x-auto">
          <table aria-label="Authority file" className="w-full border-collapse text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-muted-foreground">
              <tr className="border-b border-border">
                <th className={cn(cellClass, 'w-8')}>
                  <input type="checkbox" className="h-4 w-4 accent-primary" aria-label="Select all shown" checked={allChosen}
                    onChange={event => setSelected(event.target.checked ? items.map(item => ({ id: item.id, display_name: item.display_name })) : [])} />
                </th>
                <th className={cellClass}>Name</th>
                <th className={cellClass}>Type</th>
                <th className={cellClass}>Language</th>
                <th className={cn(cellClass, 'text-right')}>Articles</th>
                <th className={cn(cellClass, 'text-right')}>Other names</th>
                <th className={cellClass}>Status</th>
                <th className={cellClass}>Wikidata</th>
              </tr>
            </thead>
            <tbody>
              {items.map(item => (
                <tr key={item.id} className={cn('border-b border-border/60', view.entity === item.id && 'bg-accent/40')}>
                  <td className={cellClass}>
                    <input type="checkbox" className="h-4 w-4 accent-primary" aria-label={`Select ${item.display_name}`} checked={chosen.has(item.id)} onChange={event => toggle(item, event.target.checked)} />
                  </td>
                  <td className={cn(cellClass, 'whitespace-normal')}>
                    <button type="button" className="border-0 bg-transparent p-0 text-left font-semibold text-foreground hover:text-primary" onClick={() => onOpen(item.id)}>{item.display_name}</button>
                    {item.hidden && <span className={cn(chipClass, 'ml-2')}>Hidden</span>}
                  </td>
                  <td className={cellClass}>{typeLabel(item.entity_type)}</td>
                  <td className={cellClass}>{item.language}</td>
                  <td className={cn(cellClass, 'text-right tabular-nums')}>{item.article_count}</td>
                  <td className={cn(cellClass, 'text-right tabular-nums')}>{item.variant_count}</td>
                  <td className={cellClass}>{item.status === 'established' ? 'Established' : 'Provisional'}</td>
                  <td className={cellClass}>{item.qid ? <a className="font-mono hover:text-primary" href={qidHref(item.qid)} target="_blank" rel="noreferrer">{item.qid}</a> : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {pages.hasNextPage && (
        <div><button type="button" className={ghostButtonClass} disabled={pages.isFetchingNextPage} onClick={() => pages.fetchNextPage()}>
          {pages.isFetchingNextPage ? 'Loading…' : 'Load more names'}
        </button></div>
      )}
    </>
  )
}

/** A text filter typed freely, written to the URL as it changes. */
function useText(value: string, write: (text: string) => void): [string, (text: string) => void] {
  const [text, setText] = useState(value)
  useEffect(() => { setText(value) }, [value])
  return [text, next => { setText(next); write(next) }]
}

function NamesTab({ view, go }: { view: AuthoritiesView; go(next: Partial<AuthoritiesView>): void }) {
  const [text, setText] = useText(view.q, q => go({ q }))
  const typeOptions = ENTITY_TYPES.map(value => ({ value, label: typeLabel(value) }))
  return (
    <GlassPanel className="flex flex-col gap-4 p-6">
      <div className="flex flex-wrap items-end gap-4">
        <label className={labelClass}>Find a name
          <input className={fieldClass} placeholder="Any of its names" maxLength={200} value={text} onChange={event => setText(event.target.value)} />
        </label>
        <details className="relative">
          <summary className={cn(fieldClass, 'cursor-pointer list-none')}>{view.type.length ? `Type: ${view.type.map(typeLabel).join(', ')}` : 'Type: any'}</summary>
          <div className="absolute z-20 mt-1 w-56 rounded-lg border border-border bg-background p-2 shadow-lg">
            <CheckboxPicker legend="Type" options={typeOptions} selected={view.type} onChange={values => go({ type: values as EntityType[] })} />
          </div>
        </details>
        <label className={labelClass}>Status
          <select className={fieldClass} value={view.status ?? ''} onChange={event => go({ status: (event.target.value || undefined) as AuthoritiesView['status'] })}>
            <option value="">Any</option>
            <option value="provisional">Provisional</option>
            <option value="established">Established</option>
          </select>
        </label>
        <label className={labelClass}>Wikidata
          <select className={fieldClass} value={view.wikidata ?? ''} onChange={event => go({ wikidata: (event.target.value || undefined) as AuthoritiesView['wikidata'] })}>
            <option value="">Any</option>
            <option value="linked">Linked</option>
            <option value="unlinked">Not linked</option>
          </select>
        </label>
        <label className={labelClass}>Sort
          <select className={fieldClass} value={view.sort} onChange={event => go({ sort: event.target.value as AuthoritiesView['sort'] })}>
            <option value="articles">Most articles</option>
            <option value="name">A–Z</option>
          </select>
        </label>
        <label className="flex items-center gap-2 self-center text-sm text-foreground">
          <input type="checkbox" className="h-4 w-4 accent-primary" checked={view.hidden} onChange={event => go({ hidden: event.target.checked })} />
          Hidden names only
        </label>
      </div>
      <AuthorityTable view={view} onOpen={entity => go({ entity })} />
    </GlassPanel>
  )
}

function RecentChanges() {
  const pages = useInfiniteQuery({
    queryKey: ['authority-history'],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => api.authorityHistory(pageParam),
    getNextPageParam: page => page.next_cursor ?? undefined,
  })
  const items = pages.data?.pages.flatMap(page => page.items) ?? []
  return (
    <GlassPanel className="flex flex-col gap-3 p-6">
      <h3 className={sectionTitle}>Recent changes</h3>
      {pages.isError && <LoadError query={pages} message="Could not load the history." />}
      {pages.isSuccess && !items.length && <p className="text-sm text-muted-foreground">No changes yet.</p>}
      {items.length > 0 && (
        <ul aria-label="Recent changes" className="list-none pl-0 flex flex-col gap-1">
          {items.map(change => (
            <li key={change.id} className="text-sm text-muted-foreground">
              {describe(change)}
              <small className="text-xs">{` · ${new Date(change.created_at).toLocaleString()}`}</small>
            </li>
          ))}
        </ul>
      )}
      {pages.hasNextPage && (
        <div><button type="button" className={ghostButtonClass} disabled={pages.isFetchingNextPage} onClick={() => pages.fetchNextPage()}>
          {pages.isFetchingNextPage ? 'Loading…' : 'Load older changes'}
        </button></div>
      )}
    </GlassPanel>
  )
}

const TAB_LABELS: Record<AuthorityTab, string> = { names: 'Names', suggestions: 'Maybe the same?', wikidata: 'Wikidata', changes: 'Changes' }

function AuthoritiesContent() {
  const router = useRouter()
  const query = useSearchParams()
  const url = query.toString()
  // The router updates the URL in a transition; until it lands, the page shows the view just asked
  // for, so a ticked box stays ticked and a second change keeps the first.
  const [asked, setAsked] = useState<{ url: string; view: AuthoritiesView } | null>(null)
  const view = asked?.url === url ? asked.view : readAuthoritiesQuery(query)
  const go = (next: Partial<AuthoritiesView>) => {
    const target = { ...view, ...next }
    setAsked({ url, view: target })
    router.replace(authoritiesHref(target))
  }
  const [language, setLanguage] = useText(view.language, value => go({ language: value }))
  const suggestions = useQuery(suggestionsQuery(view.language.trim()))
  const wikidata = useInfiniteQuery(wikidataQuery)
  const wikidataCount = wikidata.data ? `${wikidata.data.pages.flatMap(page => page.items).length}${wikidata.hasNextPage ? '+' : ''}` : ''
  const counts: Partial<Record<AuthorityTab, string>> = {
    suggestions: suggestions.data ? String(suggestions.data.items.length) : '',
    wikidata: wikidataCount,
  }

  return (
    <div className="flex flex-col gap-6 font-sans">
      <PageHeader eyebrow="Archive" title="Authority file" description="The established form of every name, the other names that point to it, and the likely duplicates to decide on.">
        <label className={labelClass}>Language
          <input className={fieldClass} placeholder="All, or a code such as el" maxLength={16} value={language} onChange={event => setLanguage(event.target.value)} />
        </label>
      </PageHeader>
      <div role="tablist" aria-label="Authority file sections" className="flex flex-wrap gap-1 border-b border-border">
        {(Object.keys(TAB_LABELS) as AuthorityTab[]).map(tab => (
          <button key={tab} type="button" role="tab" aria-selected={view.tab === tab} onClick={() => go({ tab })}
            className={cn('-mb-px flex w-auto items-center gap-2 border-0 border-b-2 bg-transparent px-4 py-2 text-sm font-semibold', view.tab === tab ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground')}>
            {TAB_LABELS[tab]}
            {counts[tab] ? <span className="rounded-full bg-accent px-2 py-0.5 text-xs font-medium text-accent-foreground">{counts[tab]}</span> : null}
          </button>
        ))}
      </div>
      <div className={cn('grid items-start gap-6', view.entity && 'xl:grid-cols-[minmax(0,1fr)_26rem]')}>
        <div role="tabpanel" aria-label={TAB_LABELS[view.tab]} className="min-w-0">
          {view.tab === 'names' && <NamesTab view={view} go={go} />}
          {view.tab === 'suggestions' && <SuggestionQueue language={view.language.trim()} />}
          {view.tab === 'wikidata' && <WikidataReview />}
          {view.tab === 'changes' && <RecentChanges />}
        </div>
        {view.entity && (
          <AuthorityPanel key={view.entity} entityId={view.entity} onClose={() => go({ entity: undefined })} onOpen={entity => go({ entity })}
            className="xl:sticky xl:inset-auto xl:top-4 xl:z-auto xl:max-h-[calc(100vh-2rem)] xl:w-auto xl:max-w-none xl:rounded-xl xl:border xl:shadow-none" />
        )}
      </div>
    </div>
  )
}

/** The authority file: every entity's established form in a table, the queues to decide on, and what changed. */
export default function AuthoritiesPage() {
  return <Suspense fallback={null}><AuthoritiesContent /></Suspense>
}
