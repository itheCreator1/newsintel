'use client'

import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useState } from 'react'
import { ApiError, api, exportHref } from '../../lib/api'
import { BarChart } from '../../components/BarChart'
import { AuthorityPanel } from '../../components/AuthorityPanel'
import { EntityWikidata } from '../../components/EntityWikidata'
import { SeeAlso } from '../../components/SeeAlso'
import { GlassPanel } from '../../components/GlassPanel'
import { PageHeader } from '../../components/PageHeader'
import { WatchButton } from '../../components/WatchForm'
import { ExportMenu } from '../../components/ExportMenu'
import { typeLabel } from '../../lib/authorities'
import { cn, plural } from '../../lib/utils'
import { chipClass, fieldClass, ghostButtonClass, labelClass } from '../../lib/ui-classes'
import { clusterHref, compareHref, emptyInvestigation, entityHref, eventHref, queryFromState, refine, sourceHref, toHref } from '../../lib/investigation'
import { itemText } from '../../lib/wikidata'
import { countryLabel } from '../../lib/countries'
import { when } from '../../lib/datetime'
import { ArticleRow } from '../../components/ArticleRow'

const WINDOWS = [7, 30, 90, 365] as const
const DAY_MS = 86_400_000
const nextDay = (day: string) => new Date(Date.parse(day) + DAY_MS).toISOString().slice(0, 10)
const TABS = { overview: 'Overview', articles: 'Articles & stories', relationships: 'Relationships', catalogue: 'Catalogue' } as const
type Tab = keyof typeof TABS
const isTab = (value: string | null): value is Tab => value !== null && Object.hasOwn(TABS, value)
const SHOWN_NAMES = 3
const SHOWN_EVENTS = 5

/** The first few other names; the editor in the Catalogue tab lists them all. */
function namesLine(aliases: string[]): string | null {
  if (!aliases.length) return null
  const rest = aliases.length - SHOWN_NAMES
  return `Also known as ${aliases.slice(0, SHOWN_NAMES).join(', ')}${rest > 0 ? ` and ${rest} more` : ''}`
}

function Note({ children, error }: { children: string; error?: boolean }) {
  return <p className={error ? 'error px-6 py-4 text-sm text-destructive' : 'px-6 py-4 text-sm text-muted-foreground'}>{children}</p>
}

function EntityContent() {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const id = searchParams.get('id') ?? ''
  const currentHref = toHref(pathname, searchParams)
  const [days, setDays] = useState<number>(30)
  const [editing, setEditing] = useState(false)
  const urlTab = searchParams.get('tab')
  const [tab, setTab] = useState<Tab>(isTab(urlTab) ? urlTab : 'overview')
  // Client navigation keeps this page mounted, so another entity or Back re-reads the tab from the URL.
  const urlKey = `${id}|${urlTab ?? ''}`
  const [syncedKey, setSyncedKey] = useState(urlKey)
  if (syncedKey !== urlKey) {
    setSyncedKey(urlKey)
    setTab(isTab(urlTab) ? urlTab : 'overview')
  }

  function openTab(next: Tab) {
    setTab(next)
    const query = new URLSearchParams(searchParams)
    if (next === 'overview') query.delete('tab')
    else query.set('tab', next)
    router.replace(toHref(pathname, query))
  }

  // Each tab's evidence loads when the tab is shown.
  const dossier = useQuery({ queryKey: ['entity', id, days], queryFn: () => api.entityDossier(id, days), enabled: Boolean(id), retry: false })
  const wikidata = useQuery({ queryKey: ['entity-wikidata', id], queryFn: () => api.wikidata(id), enabled: Boolean(id), retry: false })
  const events = useQuery({ queryKey: ['entity-events', id], queryFn: () => api.events({ entity_id: id }), enabled: Boolean(id) && tab === 'overview', retry: false })
  const relationships = useQuery({ queryKey: ['entity-relationships', id, days], queryFn: () => api.entityRelationships(id, days), enabled: Boolean(id) && tab === 'relationships', retry: false })
  const articles = useInfiniteQuery({
    queryKey: ['entity-articles', id], initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => api.entityArticles(id, pageParam), getNextPageParam: page => page.next_cursor ?? undefined, enabled: Boolean(id) && tab === 'articles', retry: false,
  })
  const clusters = useInfiniteQuery({
    queryKey: ['entity-clusters', id], initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => api.entityClusters(id, pageParam), getNextPageParam: page => page.next_cursor ?? undefined, enabled: Boolean(id) && tab === 'articles', retry: false,
  })

  const entity = dossier.data
  const articleItems = articles.data?.pages.flatMap(page => page.items) ?? []
  const clusterItems = clusters.data?.pages.flatMap(page => page.items) ?? []
  const searchState = refine(emptyInvestigation(), 'entity_id', id)
  const searchHref = toHref('/search', queryFromState(searchState))
  const timelineItems = (entity?.timeline ?? []).map(day => ({ id: day.date, label: day.date, value: day.mentions }))
  const item = wikidata.data?.item
  const description = item && entity ? itemText(item.descriptions, entity.language) : null
  const names = entity && entity.aliases_status !== 'unavailable' ? namesLine(entity.aliases ?? []) : null
  const eventItems = events.data?.items.slice(0, SHOWN_EVENTS) ?? []

  if (!id) {
    return (
      <div className="flex flex-col gap-6 font-sans">
        <PageHeader eyebrow="Entity" title="Dossier" />
        <GlassPanel className="p-0"><Note>Choose an entity from an article, the graph, or search to open its dossier.</Note></GlassPanel>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-6 font-sans">
      <PageHeader eyebrow="Entity" title="Dossier">
        <label className={labelClass}>Window
          <select className={fieldClass} value={days} onChange={event => setDays(Number(event.target.value))}>
            {WINDOWS.map(option => <option key={option} value={option}>Last {option} days</option>)}
          </select>
        </label>
      </PageHeader>

      <GlassPanel className="overflow-hidden p-0">
        {dossier.isPending && <Note>Loading entity…</Note>}
        {dossier.isError && (dossier.error instanceof ApiError && dossier.error.status === 404
          ? <Note error>Entity not found.</Note>
          : <Note error>Could not load this entity.</Note>)}
        {entity && (
          <div className="flex flex-col gap-2 px-6 py-4">
            {entity.redirected_from && <p role="status" className="text-sm text-muted-foreground">Opened from a name merged into this entity.</p>}
            <h3 className="text-lg font-semibold text-foreground">{entity.display_name}</h3>
            {description && <p className="text-sm text-foreground">{description}</p>}
            <p className="text-sm text-muted-foreground">{entity.entity_type} · {entity.language}</p>
            {names && <p className="text-sm text-muted-foreground">{names}</p>}
            <p className="text-sm text-foreground">{`${plural(entity.total_mentions, 'mention')} · ${plural(entity.article_count, 'article')} · ${plural(entity.cluster_count, 'story', 'stories')}`}</p>
            <p className="text-sm text-muted-foreground">First seen {when(entity.first_seen_at)} · Last seen {when(entity.last_seen_at)}</p>
            <div className="flex flex-wrap gap-2">
              <Link className={chipClass} href={searchHref}>Search articles with this entity</Link>
              <Link className={chipClass} href={toHref('/events', new URLSearchParams({ entity_id: id }))}>Events with this entity</Link>
              <Link className={chipClass} href={compareHref({ kind: 'entity', a: id })}>Compare with another entity</Link>
            </div>
            <WatchButton key={id} className="self-start" kind="entity" state={searchState} defaultName={entity.display_name} label="Watch entity" />
          </div>
        )}
      </GlassPanel>

      <div role="tablist" aria-label="Entity sections" className="flex flex-wrap gap-1 border-b border-border">
        {(Object.keys(TABS) as Tab[]).map(name => (
          <button key={name} type="button" role="tab" aria-selected={tab === name} onClick={() => openTab(name)}
            className={cn('-mb-px w-auto border-0 border-b-2 bg-transparent px-4 py-2 text-sm font-semibold', tab === name ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground')}>
            {TABS[name]}
          </button>
        ))}
      </div>

      <div role="tabpanel" aria-label={TABS[tab]} className="flex flex-col gap-6">
        {tab === 'overview' && (
          <>
            {entity && (
              <GlassPanel className="flex flex-col gap-3">
                <h3 className="text-sm font-semibold text-foreground">Mentions per day (last {entity.timeline_days} days)</h3>
                {timelineItems.length === 0
                  ? <p className="text-sm text-muted-foreground">No mentions in this window.</p>
                  : <BarChart items={timelineItems} valueLabel="Mentions" ariaLabel="Daily mentions of this entity. Click a bar to search that day's articles."
                      onSelect={item => router.push(toHref('/search', queryFromState({ ...searchState, after: item.id, before: nextDay(item.id) })))} />}
              </GlassPanel>
            )}
            <GlassPanel aria-label="Events" className="flex flex-col gap-3">
              <div className="flex items-center justify-between gap-3">
                <h3 className="text-sm font-semibold text-foreground">Events</h3>
                <Link className={chipClass} href={toHref('/events', new URLSearchParams({ entity_id: id, all: '1' }))}>All events with this entity</Link>
              </div>
              {events.isPending && <p className="text-sm text-muted-foreground">Loading events…</p>}
              {events.isError && <p className="error text-sm text-destructive">Could not load events.</p>}
              {events.data && !eventItems.length && <p className="text-sm text-muted-foreground">No events with this entity yet.</p>}
              {eventItems.length > 0 && (
                <ul className="list-none pl-0 flex flex-col gap-2">
                  {eventItems.map(event => (
                    <li key={event.id} className="flex flex-col gap-0.5">
                      <Link className="text-[15px] font-semibold text-foreground hover:underline" href={eventHref(event.id, currentHref)}>{event.headline ?? 'Untitled event'}</Link>
                      <span className="text-xs text-muted-foreground">{when(event.started_at)} – {when(event.ended_at)} · {plural(event.cluster_count, 'story', 'stories')} · {plural(event.article_count, 'article')}</span>
                    </li>
                  ))}
                </ul>
              )}
            </GlassPanel>
          </>
        )}
        {tab === 'articles' && (
          <div className="grid gap-6 lg:grid-cols-2">
            <GlassPanel className="overflow-hidden p-0">
              <div className="flex items-center justify-between gap-3 px-6 pt-4">
                <h3 className="text-sm font-semibold text-foreground">Recent articles</h3>
                <ExportMenu hrefFor={format => exportHref.entity(id, format)} />
              </div>
              {articles.isPending && <Note>Loading articles…</Note>}
              {articles.isError && <Note error>Could not load articles.</Note>}
              {articles.data && !articleItems.length && <Note>No articles yet.</Note>}
              {articleItems.map(article => (
                <ArticleRow key={article.id} className="border-border px-6 py-3" article={article} href={toHref('/articles', new URLSearchParams({ article: article.id, from: currentHref }))} />
              ))}
              {articles.hasNextPage && <div className="px-6 py-4"><button className={ghostButtonClass} disabled={articles.isFetchingNextPage} onClick={() => articles.fetchNextPage()}>{articles.isFetchingNextPage ? 'Loading…' : 'Load more articles'}</button></div>}
            </GlassPanel>

            <GlassPanel className="overflow-hidden p-0">
              <h3 className="px-6 pt-4 text-sm font-semibold text-foreground">Recent stories</h3>
              {clusters.isPending && <Note>Loading stories…</Note>}
              {clusters.isError && <Note error>Could not load stories.</Note>}
              {clusters.data && !clusterItems.length && <Note>No stories yet.</Note>}
              {clusterItems.map(cluster => (
                <div key={cluster.id} className="flex flex-col gap-0.5 border-border px-6 py-3">
                  <Link className="text-[15px] font-semibold text-foreground hover:underline" href={clusterHref(cluster.id, currentHref)}>{cluster.representative_article?.title ?? 'Story'}</Link>
                  <span className="text-xs text-muted-foreground">{plural(cluster.article_count, 'article')} · {plural(cluster.source_count, 'source')} · {when(cluster.last_published_at)}</span>
                </div>
              ))}
              {clusters.hasNextPage && <div className="px-6 py-4"><button className={ghostButtonClass} disabled={clusters.isFetchingNextPage} onClick={() => clusters.fetchNextPage()}>{clusters.isFetchingNextPage ? 'Loading…' : 'Load more stories'}</button></div>}
            </GlassPanel>
          </div>
        )}
        {tab === 'relationships' && (
          <>
            {entity && (
              <GlassPanel className="flex flex-col gap-3">
                <h3 className="text-sm font-semibold text-foreground">See also</h3>
                <SeeAlso key={entity.id} entityId={entity.id} />
              </GlassPanel>
            )}
            <GlassPanel className="flex flex-col gap-4">
              <h3 className="text-sm font-semibold text-foreground">Co-occurrence in the last {days} days</h3>
              {relationships.isPending && <p className="text-sm text-muted-foreground">Loading relationships…</p>}
              {relationships.isError && <p className="error text-sm text-destructive">Could not load relationships.</p>}
              {relationships.data && (
                <>
                  <div>
                    <strong className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Entities</strong>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {relationships.data.entities.map(related => <Link key={related.id} className={chipClass} href={entityHref(related.id)}>{related.display_name} ({related.entity_type}) · {plural(related.article_count, 'article')}</Link>)}
                      {!relationships.data.entities.length && <span className="text-sm text-muted-foreground">No related entities.</span>}
                    </div>
                  </div>
                  <div>
                    <strong className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Countries</strong>
                    <ul className="list-none pl-0 mt-2 flex flex-wrap gap-2">
                      {relationships.data.countries.map(country => <li key={`${country.country_code}-${country.role}`} className="text-sm text-foreground">{`${countryLabel(country.country_code)} · ${country.role} · ${plural(country.article_count, 'article')}`}</li>)}
                      {!relationships.data.countries.length && <li className="text-sm text-muted-foreground">No countries.</li>}
                    </ul>
                  </div>
                  <div>
                    <strong className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Sources</strong>
                    <ul className="list-none pl-0 mt-2 flex flex-wrap gap-2">
                      {relationships.data.feeds.map(feed => <li key={feed.id}><Link className="text-sm text-foreground hover:underline" href={sourceHref(feed.id, currentHref)}>{`${feed.name} · ${plural(feed.article_count, 'article')}`}</Link></li>)}
                      {!relationships.data.feeds.length && <li className="text-sm text-muted-foreground">No sources.</li>}
                    </ul>
                  </div>
                </>
              )}
            </GlassPanel>
          </>
        )}
        {tab === 'catalogue' && (
          <>
            {entity && (
              <GlassPanel className="flex flex-wrap items-center gap-3">
                <h3 className="text-sm font-semibold text-foreground">Authority file</h3>
                <span className={chipClass}>{entity.status === 'established' ? 'Established' : 'Provisional'}</span>
                <span className={chipClass}>{typeLabel(entity.entity_type)}</span>
                {entity.hidden && <span className={chipClass}>Hidden</span>}
                <button type="button" className={ghostButtonClass} onClick={() => setEditing(true)}>Edit</button>
              </GlassPanel>
            )}
            {entity && (
              <GlassPanel className="flex flex-col gap-3">
                <h3 className="text-sm font-semibold text-foreground">Wikidata</h3>
                <EntityWikidata key={entity.id} entityId={entity.id} language={entity.language} />
              </GlassPanel>
            )}
          </>
        )}
      </div>

      {entity && editing && (
        <AuthorityPanel key={entity.id} entityId={entity.id} entity={entity} onDossier onClose={() => setEditing(false)} />
      )}
    </div>
  )
}

export default function EntitiesPage() {
  return <Suspense fallback={null}><EntityContent /></Suspense>
}
