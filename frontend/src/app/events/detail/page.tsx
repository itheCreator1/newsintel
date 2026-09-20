'use client'

import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import Link from 'next/link'
import { usePathname, useSearchParams } from 'next/navigation'
import { Suspense, useState } from 'react'
import { ApiError, api } from '../../../lib/api'
import { BarChart } from '../../../components/BarChart'
import { GlassPanel } from '../../../components/GlassPanel'
import { PageHeader } from '../../../components/PageHeader'
import { StatusBadge } from '../../../components/StatusBadge'
import { plural } from '../../../lib/utils'
import { chipClass, ghostButtonClass } from '../../../lib/ui-classes'
import { clusterHref, entityHref, toHref } from '../../../lib/investigation'

const when = (value: string | null | undefined) => value ? new Date(value).toLocaleString() : '—'
const pageOf = <T extends { next_cursor: string | null }>(load: (cursor?: string) => Promise<T>) => ({
  initialPageParam: undefined as string | undefined,
  queryFn: ({ pageParam }: { pageParam: string | undefined }) => load(pageParam),
  getNextPageParam: (page: T) => page.next_cursor ?? undefined,
  retry: false,
})

function Note({ children, error }: { children: string; error?: boolean }) {
  return <p className={error ? 'error px-6 py-4 text-sm text-destructive' : 'px-6 py-4 text-sm text-muted-foreground'}>{children}</p>
}

// The stored signals are exactly what the engine scored, shown as-is so a join can be audited.
const signalText = (signals: Record<string, unknown>) =>
  Object.entries(signals).map(([name, value]) => `${name} ${typeof value === 'number' ? value.toFixed(2) : String(value)}`).join(' · ')

function EventContent() {
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const id = searchParams.get('id') ?? ''
  const from = searchParams.get('from') ?? ''
  const currentHref = toHref(pathname, searchParams)
  const [selectedDay, setSelectedDay] = useState<string | null>(null)

  const event = useQuery({ queryKey: ['event', id], queryFn: () => api.event(id), enabled: Boolean(id), retry: false })
  // Evidence waits for the header, so a missing event costs one request.
  const ready = Boolean(id) && event.isSuccess
  const timeline = useInfiniteQuery({ queryKey: ['event-timeline', id], ...pageOf(after => api.eventTimeline(id, after)), enabled: ready })
  const clusters = useInfiniteQuery({ queryKey: ['event-clusters', id], ...pageOf(cursor => api.eventClusters(id, cursor)), enabled: ready })
  const articles = useInfiniteQuery({ queryKey: ['event-articles', id], ...pageOf(cursor => api.eventArticles(id, cursor)), enabled: ready })

  const days = timeline.data?.pages.flatMap(page => page.items) ?? []
  const clusterItems = clusters.data?.pages.flatMap(page => page.items) ?? []
  const articleItems = articles.data?.pages.flatMap(page => page.items) ?? []
  const selected = days.find(day => day.date === selectedDay) ?? days[0]
  const data = event.data
  const openArticle = (articleId: string) => toHref('/articles', new URLSearchParams({ article: articleId, from: currentHref }))

  if (!id) {
    return (
      <div className="flex flex-col gap-6 font-sans">
        <PageHeader eyebrow="Event" title="Dossier" />
        <GlassPanel className="p-0"><Note>Choose an event from the events list to open its dossier.</Note></GlassPanel>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-6 font-sans">
      <PageHeader eyebrow="Event" title="Dossier">
        <Link className={chipClass} href={from.startsWith('/events/') ? from : '/events/'}>Back to events</Link>
      </PageHeader>

      <GlassPanel className="overflow-hidden p-0">
        {event.isPending && <Note>Loading event…</Note>}
        {event.isError && (event.error instanceof ApiError && event.error.status === 404
          ? <Note error>Event not found.</Note>
          : <Note error>Could not load this event.</Note>)}
        {data && (
          <div className="flex flex-col gap-2 px-6 py-4">
            <div className="flex items-start justify-between gap-3">
              <h3 className="text-lg font-semibold text-foreground">{data.headline ?? 'Untitled event'}</h3>
              <StatusBadge tone={data.status === 'active' ? 'healthy' : 'pending'}>{data.status}</StatusBadge>
            </div>
            <p className="text-sm text-foreground">{`${plural(data.cluster_count, 'story', 'stories')} · ${plural(data.article_count, 'article')} · ${plural(data.source_count, 'source')}`}</p>
            <p className="text-sm text-muted-foreground">{when(data.started_at)} – {when(data.ended_at)}{data.primary_country ? ` · Country ${data.primary_country}` : ''}</p>
            <p className="text-xs text-muted-foreground">Algorithm {data.algorithm_version} · created {when(data.created_at)} · updated {when(data.updated_at)}</p>
            {data.entities.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {data.entities.map(entity => <Link key={entity.id} className={chipClass} href={entityHref(entity.id)}>{entity.display_name} ({entity.entity_type}) · {plural(entity.article_count, 'article')}</Link>)}
              </div>
            )}
          </div>
        )}
      </GlassPanel>

      {data && (
        <GlassPanel className="flex flex-col gap-3">
          <h3 className="text-sm font-semibold text-foreground">Articles per day (UTC)</h3>
          {timeline.isPending && <p className="text-sm text-muted-foreground">Loading timeline…</p>}
          {timeline.isError && <p className="error text-sm text-destructive">Could not load the timeline.</p>}
          {timeline.data && !days.length && <p className="text-sm text-muted-foreground">No dated articles yet.</p>}
          {days.length > 0 && (
            <BarChart items={days.map(day => ({ id: day.date, label: day.date, value: day.article_count, highlight: day.date === selected?.date }))}
              valueLabel="Articles" ariaLabel="Articles per day. Click a bar to see that day's earliest articles." onSelect={item => setSelectedDay(item.id)} />
          )}
          {timeline.hasNextPage && <div><button className={ghostButtonClass} disabled={timeline.isFetchingNextPage} onClick={() => timeline.fetchNextPage()}>{timeline.isFetchingNextPage ? 'Loading…' : 'Load later days'}</button></div>}
          {selected && (
            <section aria-label="Day details" className="flex flex-col gap-1.5 border-t border-border pt-3">
              <h4 className="text-sm font-semibold text-foreground">{selected.date}</h4>
              <p className="text-xs text-muted-foreground">{`${plural(selected.article_count, 'article')} · ${plural(selected.source_count, 'source')} · ${selected.clusters_started} ${selected.clusters_started === 1 ? 'story' : 'stories'} started`}</p>
              {selected.evidence.map(item => <Link key={item.article_id} className="text-sm font-semibold text-foreground hover:underline" href={openArticle(item.article_id)}>{item.title}</Link>)}
            </section>
          )}
        </GlassPanel>
      )}

      {data && (
        <div className="grid gap-6 lg:grid-cols-2">
          <GlassPanel className="overflow-hidden p-0">
            <h3 className="px-6 pt-4 text-sm font-semibold text-foreground">Stories</h3>
            {clusters.isPending && <Note>Loading stories…</Note>}
            {clusters.isError && <Note error>Could not load stories.</Note>}
            {clusters.data && !clusterItems.length && <Note>No stories yet.</Note>}
            {clusterItems.map(cluster => (
              <div key={cluster.id} className="flex flex-col gap-0.5 border-border px-6 py-3">
                <Link className="text-[15px] font-semibold text-foreground hover:underline" href={clusterHref(cluster.id, currentHref)}>{cluster.representative_article?.title ?? 'Story'}</Link>
                <span className="text-xs text-muted-foreground">{plural(cluster.article_count, 'article')} · {plural(cluster.source_count, 'source')}</span>
                <span className="text-xs text-muted-foreground">Joined with score {cluster.score.toFixed(2)}</span>
                <span className="text-xs text-muted-foreground">{signalText(cluster.signals ?? {})}</span>
              </div>
            ))}
            {clusters.hasNextPage && <div className="px-6 py-4"><button className={ghostButtonClass} disabled={clusters.isFetchingNextPage} onClick={() => clusters.fetchNextPage()}>{clusters.isFetchingNextPage ? 'Loading…' : 'Load more stories'}</button></div>}
          </GlassPanel>

          <GlassPanel className="overflow-hidden p-0">
            <h3 className="px-6 pt-4 text-sm font-semibold text-foreground">Articles</h3>
            {articles.isPending && <Note>Loading articles…</Note>}
            {articles.isError && <Note error>Could not load articles.</Note>}
            {articles.data && !articleItems.length && <Note>No articles yet.</Note>}
            {articleItems.map(article => (
              <div key={article.id} className="flex flex-col gap-0.5 border-border px-6 py-3">
                <Link className="text-[15px] font-semibold text-foreground hover:underline" href={openArticle(article.id)}>{article.title}</Link>
                <span className="text-xs text-muted-foreground">{when(article.published_at ?? article.first_discovered_at)} · <Link className="hover:underline" href={clusterHref(article.cluster_id, currentHref)}>Story</Link></span>
              </div>
            ))}
            {articles.hasNextPage && <div className="px-6 py-4"><button className={ghostButtonClass} disabled={articles.isFetchingNextPage} onClick={() => articles.fetchNextPage()}>{articles.isFetchingNextPage ? 'Loading…' : 'Load more articles'}</button></div>}
          </GlassPanel>
        </div>
      )}
    </div>
  )
}

export default function EventDetailPage() {
  return <Suspense fallback={null}><EventContent /></Suspense>
}
