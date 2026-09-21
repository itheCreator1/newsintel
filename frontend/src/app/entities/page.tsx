'use client'

import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useState } from 'react'
import { ApiError, api } from '../../lib/api'
import { BarChart } from '../../components/BarChart'
import { GlassPanel } from '../../components/GlassPanel'
import { PageHeader } from '../../components/PageHeader'
import { WatchForm } from '../../components/WatchForm'
import { plural } from '../../lib/utils'
import { chipClass, fieldClass, ghostButtonClass, labelClass } from '../../lib/ui-classes'
import { clusterHref, compareHref, emptyInvestigation, entityHref, queryFromState, refine, sourceHref, toHref } from '../../lib/investigation'

const WINDOWS = [7, 30, 90, 365] as const
const DAY_MS = 86_400_000
const when = (value: string | null | undefined) => value ? new Date(value).toLocaleString() : '—'
const nextDay = (day: string) => new Date(Date.parse(day) + DAY_MS).toISOString().slice(0, 10)

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

  const dossier = useQuery({ queryKey: ['entity', id, days], queryFn: () => api.entityDossier(id, days), enabled: Boolean(id), retry: false })
  const relationships = useQuery({ queryKey: ['entity-relationships', id, days], queryFn: () => api.entityRelationships(id, days), enabled: Boolean(id), retry: false })
  const articles = useInfiniteQuery({
    queryKey: ['entity-articles', id], initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => api.entityArticles(id, pageParam), getNextPageParam: page => page.next_cursor ?? undefined, enabled: Boolean(id), retry: false,
  })
  const clusters = useInfiniteQuery({
    queryKey: ['entity-clusters', id], initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => api.entityClusters(id, pageParam), getNextPageParam: page => page.next_cursor ?? undefined, enabled: Boolean(id), retry: false,
  })

  const entity = dossier.data
  const articleItems = articles.data?.pages.flatMap(page => page.items) ?? []
  const clusterItems = clusters.data?.pages.flatMap(page => page.items) ?? []
  const searchState = refine(emptyInvestigation(), 'entity_id', id)
  const searchHref = toHref('/search', queryFromState(searchState))
  const timelineItems = (entity?.timeline ?? []).map(day => ({ id: day.date, label: day.date, value: day.mentions }))

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
            <h3 className="text-lg font-semibold text-foreground">{entity.display_name}</h3>
            <p className="text-sm text-muted-foreground">{entity.entity_type} · {entity.language}</p>
            <p className="text-sm text-muted-foreground">
              {entity.aliases_status === 'unavailable' ? 'Aliases unavailable' : (entity.aliases ?? []).join(', ') || 'No aliases'}
            </p>
            <p className="text-sm text-foreground">{`${plural(entity.total_mentions, 'mention')} · ${plural(entity.article_count, 'article')} · ${plural(entity.cluster_count, 'story', 'stories')}`}</p>
            <p className="text-sm text-muted-foreground">First seen {when(entity.first_seen_at)} · Last seen {when(entity.last_seen_at)}</p>
            <div className="flex flex-wrap gap-2">
              <Link className={chipClass} href={searchHref}>Search articles with this entity</Link>
              <Link className={chipClass} href={toHref('/events', new URLSearchParams({ entity_id: id }))}>Events with this entity</Link>
              <Link className={chipClass} href={compareHref({ kind: 'entity', a: id })}>Compare with another entity</Link>
            </div>
            <WatchForm key={id} className="" kind="entity" state={searchState} defaultName={entity.display_name} label="Watch entity" />
          </div>
        )}
      </GlassPanel>

      {entity && (
        <GlassPanel className="flex flex-col gap-3">
          <h3 className="text-sm font-semibold text-foreground">Mentions per day (last {entity.timeline_days} days)</h3>
          {timelineItems.length === 0
            ? <p className="text-sm text-muted-foreground">No mentions in this window.</p>
            : <BarChart items={timelineItems} valueLabel="Mentions" ariaLabel="Daily mentions of this entity. Click a bar to search that day's articles."
                onSelect={item => router.push(toHref('/search', queryFromState({ ...searchState, after: item.id, before: nextDay(item.id) })))} />}
        </GlassPanel>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <GlassPanel className="overflow-hidden p-0">
          <h3 className="px-6 pt-4 text-sm font-semibold text-foreground">Recent articles</h3>
          {articles.isPending && <Note>Loading articles…</Note>}
          {articles.isError && <Note error>Could not load articles.</Note>}
          {articles.data && !articleItems.length && <Note>No articles yet.</Note>}
          {articleItems.map(article => (
            <div key={article.id} className="flex flex-col gap-0.5 border-border px-6 py-3">
              <Link className="text-[15px] font-semibold text-foreground hover:underline" href={toHref('/articles', new URLSearchParams({ article: article.id, from: currentHref }))}>{article.title}</Link>
              <span className="text-xs text-muted-foreground">{when(article.published_at ?? article.first_discovered_at)}</span>
            </div>
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
              <ul className="mt-2 flex flex-wrap gap-2">
                {relationships.data.countries.map(country => <li key={`${country.country_code}-${country.role}`} className="text-sm text-foreground">{`${country.country_code} · ${country.role} · ${plural(country.article_count, 'article')}`}</li>)}
                {!relationships.data.countries.length && <li className="text-sm text-muted-foreground">No countries.</li>}
              </ul>
            </div>
            <div>
              <strong className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Sources</strong>
              <ul className="mt-2 flex flex-wrap gap-2">
                {relationships.data.feeds.map(feed => <li key={feed.id}><Link className="text-sm text-foreground hover:underline" href={sourceHref(feed.id, currentHref)}>{`${feed.name} · ${plural(feed.article_count, 'article')}`}</Link></li>)}
                {!relationships.data.feeds.length && <li className="text-sm text-muted-foreground">No sources.</li>}
              </ul>
            </div>
          </>
        )}
      </GlassPanel>
    </div>
  )
}

export default function EntitiesPage() {
  return <Suspense fallback={null}><EntityContent /></Suspense>
}
