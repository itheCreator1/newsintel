'use client'

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Link from 'next/link'
import { useState, type ReactNode } from 'react'
import { LoadError } from '../../components/Feedback'
import { GlassPanel } from '../../components/GlassPanel'
import { PageHeader } from '../../components/PageHeader'
import { api, ApiError } from '../../lib/api'
import type { AuthorityFilters, AuthorityHistoryPage, AuthoritySuggestionList, WikidataReviewItem } from '../../lib/api-types'
import { entityHref } from '../../lib/investigation'
import { chipClass, fieldClass, ghostButtonClass, labelClass } from '../../lib/ui-classes'
import { Attribution } from '../../components/EntityWikidata'
import { qidHref, reasonText as wikidataReason, wikidataChange } from '../../lib/wikidata'

type Suggestion = AuthoritySuggestionList['items'][number]
type Change = AuthorityHistoryPage['items'][number]
type Name = Suggestion['root']

const plural = (count: number, word: string, many = `${word}s`) => `${count} ${count === 1 ? word : many}`
const failure = (error: unknown, fallback: string) => error instanceof ApiError ? error.message : fallback
const sectionTitle = 'text-xs font-semibold uppercase tracking-wide text-muted-foreground'

function EntityLink({ id, name }: { id: string | null; name: string | null }) {
  if (!id || !name) return <span>{name ?? 'Unknown entity'}</span>
  return <Link className="font-medium text-foreground hover:text-primary" href={entityHref(id)}>{name}</Link>
}

/** One change in words, naming both entities when it has two. */
function describe(change: Change): ReactNode {
  const after = change.after ?? {}
  const entity = <EntityLink id={change.entity_id} name={change.entity_name} />
  const other = <EntityLink id={change.other_id} name={change.other_name ?? (change.other_id ? 'another entity' : null)} />
  switch (change.action) {
    case 'merged': return <>{entity} merged into {other}</>
    case 'split': return <>{entity} split back out of {other}</>
    case 'renamed': return <>{entity}{after.preferred_text ? `: renamed to ${String(after.preferred_text)}` : ': name reset to the spelling in the articles'}</>
    case 'status_changed': return <>{entity}: marked {String(after.status)}</>
    case 'ambiguous_changed': return <>{entity}{after.ambiguous ? ': marked as an ambiguous name' : ': no longer marked as ambiguous'}</>
    case 'distinct_added': return <>{entity} and {other} recorded as different</>
    case 'distinct_removed': return <>{entity} and {other} no longer recorded as different</>
    case 'relation_added': return <>{entity} linked to {other}</>
    case 'relation_changed': return <>{entity}: link to {other} changed</>
    case 'relation_removed': return <>{entity} no longer linked to {other}</>
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

/** "Maybe the same?": approve merges the less used name into the other, reject records them as different. */
function SuggestionQueue({ language }: { language?: string }) {
  const client = useQueryClient()
  const [message, setMessage] = useState('')
  const suggestions = useQuery({ queryKey: ['authority-suggestions', language ?? ''], queryFn: () => api.authoritySuggestions(language) })
  const decision = useMutation({
    mutationFn: decide,
    onMutate: () => setMessage(''),
    onSuccess: (_result, chosen) => {
      setMessage(outcome(chosen))
      return Promise.all(['authority-suggestions', 'authorities', 'authority-history'].map(key => client.invalidateQueries({ queryKey: [key] })))
    },
  })
  const items = suggestions.data?.items ?? []
  const count = (name: Name) => plural(name.article_count, 'article')

  return (
    <GlassPanel className="flex flex-col gap-4 p-6">
      <h3 className={sectionTitle}>Maybe the same?</h3>
      <p className="text-sm text-muted-foreground">Names that look alike. Nothing is merged until you decide; the first name keeps its place unless you choose the other.</p>
      {suggestions.isPending && <p className="text-sm text-muted-foreground">Looking for likely duplicates…</p>}
      {suggestions.isError && <LoadError query={suggestions} message="Could not load suggestions." />}
      {suggestions.isSuccess && !items.length && <p className="text-sm text-muted-foreground">No likely duplicates right now.</p>}
      {message && <p role="status" className="text-sm text-primary">{message}</p>}
      {decision.isError && <p role="alert" className="error text-sm text-destructive">{failure(decision.error, 'Could not record this decision.')}</p>}
      {items.length > 0 && (
        <ul aria-label="Maybe the same?" className="list-none pl-0 flex flex-col gap-3">
          {items.map(item => {
            const { root, variant } = item
            return (
              <li key={`${root.id}-${variant.id}`} className="flex flex-col gap-2 rounded-lg border border-border p-4">
                <p className="text-sm text-foreground">{`Is “${variant.display_name}” the same as “${root.display_name}”?`}</p>
                <p className="flex flex-wrap gap-3 text-sm">
                  <EntityLink id={variant.id} name={variant.display_name} />
                  <EntityLink id={root.id} name={root.display_name} />
                </p>
                <small className="text-xs text-muted-foreground">{reasonText(item)}</small>
                <small className="text-xs text-muted-foreground">{`${root.entity_type} · ${count(variant)} / ${count(root)}`}</small>
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
  const pages = useInfiniteQuery({
    queryKey: ['wikidata-review'],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => api.wikidataCandidates(pageParam),
    getNextPageParam: page => page.next_cursor ?? undefined,
  })
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
        <h3 className={sectionTitle}>Wikidata suggestions</h3>
        <button type="button" className={ghostButtonClass} disabled={pending || !items.length} onClick={() => approve.mutate()}>Approve all exact</button>
      </div>
      <p className="text-sm text-muted-foreground">Possible Wikidata items for names not linked yet. Nothing is linked until you decide. Approve all exact links every name whose one exact label has the right type.</p>
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
        <ul aria-label="Wikidata suggestions" className="list-none pl-0 flex flex-col gap-3">
          {items.map(item => (
            <li key={`${item.entity_id}-${item.qid}`} className="flex flex-col gap-1 rounded-lg border border-border p-4">
              <p className="flex flex-wrap items-center gap-2 text-sm">
                <EntityLink id={item.entity_id} name={item.display_name} />
                <a className="font-mono text-foreground hover:text-primary" href={qidHref(item.qid)} target="_blank" rel="noreferrer">{item.qid}</a>
              </p>
              {(item.label || item.description) && <span className="text-sm text-muted-foreground">{[item.label, item.description].filter(Boolean).join(' · ')}</span>}
              <small className="text-xs text-muted-foreground">{item.reasons.map(wikidataReason).join(' · ')}</small>
              <small className="text-xs text-muted-foreground">{`${item.entity_type} · ${item.language}`}</small>
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

function AuthorityList({ filters }: { filters: AuthorityFilters }) {
  const pages = useInfiniteQuery({
    queryKey: ['authorities', filters],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => api.authorities(filters, pageParam),
    getNextPageParam: page => page.next_cursor ?? undefined,
  })
  const items = pages.data?.pages.flatMap(page => page.items) ?? []
  return (
    <>
      {pages.isPending && <p className="text-sm text-muted-foreground">Loading the authority file…</p>}
      {pages.isError && <LoadError query={pages} message="Could not load the authority file." />}
      {pages.isSuccess && !items.length && <p className="text-sm text-muted-foreground">No names match.</p>}
      {items.length > 0 && (
        <ul aria-label="Authority file" className="list-none pl-0 flex flex-col divide-y divide-border">
          {items.map(item => (
            <li key={item.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
              <div className="flex flex-col gap-1">
                <Link className="text-[15px] font-semibold text-foreground hover:text-primary" href={entityHref(item.id)}>{item.display_name}</Link>
                <small className="text-xs text-muted-foreground">
                  {[item.entity_type, item.language, ...(item.variant_count ? [plural(item.variant_count, 'other name')] : [])].join(' · ')}
                </small>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {item.qid && <a className={chipClass} href={qidHref(item.qid)} target="_blank" rel="noreferrer">{item.qid}</a>}
                <span className={chipClass}>{item.status === 'established' ? 'Established' : 'Provisional'}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
      {pages.hasNextPage && (
        <div><button type="button" className={ghostButtonClass} disabled={pages.isFetchingNextPage} onClick={() => pages.fetchNextPage()}>
          {pages.isFetchingNextPage ? 'Loading…' : 'Load more names'}
        </button></div>
      )}
    </>
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

/** The authority file: every entity's established form, the duplicate queue, and what changed. */
export default function AuthoritiesPage() {
  const [text, setText] = useState('')
  const [provisional, setProvisional] = useState(false)
  const [languageText, setLanguageText] = useState('')
  const [wikidata, setWikidata] = useState<'' | 'linked' | 'unlinked'>('')
  const q = text.trim()
  const language = languageText.trim() || undefined
  const filters: AuthorityFilters = {
    ...(q ? { q } : {}),
    ...(provisional ? { status: 'provisional' as const } : {}),
    ...(language ? { language } : {}),
    ...(wikidata ? { wikidata } : {}),
  }

  return (
    <div className="flex flex-col gap-6 font-sans">
      <PageHeader eyebrow="Archive" title="Authority file" description="The established form of every name, the other names that point to it, and the likely duplicates to decide on.">
        <label className={labelClass}>Language
          <input className={fieldClass} placeholder="All, or a code such as el" maxLength={16} value={languageText} onChange={event => setLanguageText(event.target.value)} />
        </label>
      </PageHeader>
      <SuggestionQueue language={language} />
      <WikidataReview />
      <GlassPanel className="flex flex-col gap-4 p-6">
        <h3 className={sectionTitle}>Names</h3>
        <div className="flex flex-wrap items-end gap-4">
          <label className={labelClass}>Find a name
            <input className={fieldClass} placeholder="Any of its names" maxLength={200} value={text} onChange={event => setText(event.target.value)} />
          </label>
          <label className="flex items-center gap-2 text-sm text-foreground">
            <input type="checkbox" className="h-4 w-4 accent-primary" checked={provisional} onChange={event => setProvisional(event.target.checked)} />
            Provisional only
          </label>
          <label className={labelClass}>Wikidata
            <select className={fieldClass} value={wikidata} onChange={event => setWikidata(event.target.value as typeof wikidata)}>
              <option value="">Any</option>
              <option value="linked">Linked</option>
              <option value="unlinked">Not linked</option>
            </select>
          </label>
        </div>
        <AuthorityList filters={filters} />
      </GlassPanel>
      <RecentChanges />
    </div>
  )
}
