'use client'

import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useState } from 'react'
import { ApiError, api } from '../../../lib/api'
import { BarChart } from '../../../components/BarChart'
import { GlassPanel } from '../../../components/GlassPanel'
import { PageHeader } from '../../../components/PageHeader'
import { StatusBadge } from '../../../components/StatusBadge'
import { plural } from '../../../lib/utils'
import { chipClass, fieldClass, ghostButtonClass, labelClass } from '../../../lib/ui-classes'
import { clusterHref, compareHref, emptyInvestigation, entityHref, queryFromState, refine, toHref } from '../../../lib/investigation'

const WINDOWS = [7, 30, 90]
const DAY_MS = 86_400_000
const nextDay = (day: string) => new Date(Date.parse(day) + DAY_MS).toISOString().slice(0, 10)
const when = (value: string | null | undefined) => value ? new Date(value).toLocaleString() : '—'
const minutes = (value: number) => `${Math.round(value)} min`
// `from` is only followed back to pages that link here, never to an arbitrary path.
const ORIGINS: [string, string][] = [['/sources/', 'Back to sources'], ['/entities/', 'Back to entity'], ['/articles/', 'Back to article'], ['/monitors', 'Back to monitor'], ['/compare/', 'Back to comparison']]
const pageOf = <T extends { next_cursor: string | null }>(load: (cursor?: string) => Promise<T>) => ({
  initialPageParam: undefined as string | undefined,
  queryFn: ({ pageParam }: { pageParam: string | undefined }) => load(pageParam),
  getNextPageParam: (page: T) => page.next_cursor ?? undefined,
  retry: false,
})

function Note({ children, error }: { children: string; error?: boolean }) {
  return <p className={error ? 'error px-6 py-4 text-sm text-destructive' : 'px-6 py-4 text-sm text-muted-foreground'}>{children}</p>
}

function SourceContent() {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const id = searchParams.get('id') ?? ''
  const from = searchParams.get('from') ?? ''
  const currentHref = toHref(pathname, searchParams)
  const [days, setDays] = useState<number>(30)

  const source = useQuery({ queryKey: ['source', id, days], queryFn: () => api.source(id, days), enabled: Boolean(id), retry: false })
  // Everything else waits for the header, so a missing source costs one request.
  const ready = Boolean(id) && source.isSuccess
  const coverage = useQuery({ queryKey: ['source-coverage', id, days], queryFn: () => api.sourceCoverage(id, days), enabled: ready, retry: false })
  const timing = useQuery({ queryKey: ['source-timing', id, days], queryFn: () => api.sourceTiming(id, days), enabled: ready, retry: false })
  const clusters = useInfiniteQuery({ queryKey: ['source-clusters', id], ...pageOf(cursor => api.sourceClusters(id, cursor)), enabled: ready })
  const articles = useInfiniteQuery({ queryKey: ['source-articles', id], ...pageOf(cursor => api.sourceArticles(id, cursor)), enabled: ready })
  const fetches = useInfiniteQuery({ queryKey: ['source-fetches', id], ...pageOf(cursor => api.sourceFetches(id, cursor)), enabled: ready })

  const data = source.data
  const clusterItems = clusters.data?.pages.flatMap(page => page.items) ?? []
  const articleItems = articles.data?.pages.flatMap(page => page.items) ?? []
  const fetchItems = fetches.data?.pages.flatMap(page => page.items) ?? []
  const searchState = refine(emptyInvestigation(), 'source_id', id)
  const back = ORIGINS.find(([prefix]) => from.startsWith(prefix))
  const openArticle = (articleId: string) => toHref('/articles', new URLSearchParams({ article: articleId, from: currentHref }))

  if (!id) {
    return (
      <div className="flex flex-col gap-6 font-sans">
        <PageHeader eyebrow="Source" title="Dossier" />
        <GlassPanel className="p-0"><Note>Choose a source from the sources list to open its dossier.</Note></GlassPanel>
      </div>
    )
  }

  const health = data?.health
  const badge = !data ? null : !data.enabled ? 'Disabled' : health!.consecutive_failures > 0 ? 'Failing' : data.last_success_at ? 'Healthy' : 'Awaiting poll'

  return (
    <div className="flex flex-col gap-6 font-sans">
      <PageHeader eyebrow="Source" title="Dossier">
        <label className={labelClass}>Window
          <select className={fieldClass} value={days} onChange={event => setDays(Number(event.target.value))}>
            {WINDOWS.map(option => <option key={option} value={option}>Last {option} days</option>)}
          </select>
        </label>
        <Link className={chipClass} href={compareHref({ kind: 'source', a: id })}>Compare with another source</Link>
        <Link className={chipClass} href={back ? from : '/sources/'}>{back ? back[1] : 'Back to sources'}</Link>
      </PageHeader>

      <GlassPanel className="overflow-hidden p-0">
        {source.isPending && <Note>Loading source…</Note>}
        {source.isError && (source.error instanceof ApiError && source.error.status === 404
          ? <Note error>Source not found.</Note>
          : <Note error>Could not load this source.</Note>)}
        {data && health && (
          <div className="flex flex-col gap-2 px-6 py-4">
            <div className="flex items-start justify-between gap-3">
              <h3 className="text-lg font-semibold text-foreground">{data.name}</h3>
              <StatusBadge tone={badge === 'Healthy' ? 'healthy' : 'pending'}>{badge}</StatusBadge>
            </div>
            <p className="break-all text-sm text-muted-foreground">{data.url}</p>
            <p className="text-sm text-foreground">{`Country ${data.source_country ?? '—'} · Language ${data.expected_language ?? '—'} · ${data.fetching_mode.split('_').join(' ')} · polls every ${data.poll_interval_minutes} min`}</p>
            {data.retired_at && <p className="text-sm text-muted-foreground">Retired {when(data.retired_at)}. Its archive is kept.</p>}
            {health.consecutive_failures > 0 && <p className="text-sm text-destructive">{plural(health.consecutive_failures, 'failed fetch', 'failed fetches')} in a row</p>}
            <p className="text-sm text-muted-foreground">Last attempt {health.last_attempt_status ?? '—'} · {when(health.last_attempt_at)} · last success {when(data.last_success_at)} · next poll {when(data.next_poll_at)}</p>
            {health.last_failure && <p className="text-sm text-muted-foreground">{`Last failure: ${health.last_failure.error_category ?? 'error'}${health.last_failure.error_message ? ` — ${health.last_failure.error_message}` : ''}`}</p>}
            <p className="text-xs text-muted-foreground">Articles seen from {when(data.first_seen_at)} to {when(data.last_seen_at)}</p>
          </div>
        )}
      </GlassPanel>

      {data && (
        <GlassPanel className="flex flex-col gap-2">
          <h3 className="text-sm font-semibold text-foreground">Last {data.window_days} days</h3>
          <p className="text-sm text-foreground">{`${data.publishing.with_published_at} of ${plural(data.publishing.articles, 'article')} have a publish date`}</p>
          <p className="text-sm text-foreground">{`Of ${plural(data.extraction.articles, 'article')}: ${data.extraction.extracted} extracted · ${data.extraction.failed} failed · ${data.extraction.in_progress} in progress · ${data.extraction.not_extracted} not extracted`}</p>
          {data.fetching_mode === 'rss' && <p className="text-xs text-muted-foreground">This source collects RSS metadata only, so full text is not requested for it.</p>}
          <p className="text-sm text-foreground">{`${plural(data.fetches.total, 'fetch', 'fetches')}: ${data.fetches.success} succeeded · ${data.fetches.failed} failed · ${plural(data.fetches.new_articles, 'new article')}${data.fetches.mean_duration_ms === null ? '' : ` · mean ${Math.round(data.fetches.mean_duration_ms)} ms`}`}</p>
          {Object.keys(data.fetches.failures_by_category).length > 0 && <p className="text-sm text-muted-foreground">Failures: {Object.entries(data.fetches.failures_by_category).map(([name, count]) => `${name} ${count}`).join(' · ')}</p>}
          <h4 className="pt-2 text-sm font-semibold text-foreground">Articles per day (UTC)</h4>
          <BarChart items={data.timeline.map(day => ({ id: day.date, label: day.date, value: day.article_count }))} valueLabel="Articles"
            ariaLabel="Articles published per day. Click a bar to search that day's articles."
            onSelect={item => router.push(toHref('/search', queryFromState({ ...searchState, after: item.id, before: nextDay(item.id) })))} />
        </GlassPanel>
      )}

      {data && (
        <div className="grid gap-6 lg:grid-cols-2">
          <GlassPanel className="flex flex-col gap-2">
            <h3 className="text-sm font-semibold text-foreground">Coverage</h3>
            {coverage.isPending && <p className="text-sm text-muted-foreground">Loading coverage…</p>}
            {coverage.isError && <p className="error text-sm text-destructive">Could not load coverage.</p>}
            {coverage.data && (
              <>
                <div className="flex flex-wrap gap-2">
                  {coverage.data.entities.map(entity => <Link key={entity.id} className={chipClass} href={entityHref(entity.id)}>{entity.display_name} ({entity.entity_type}) · {plural(entity.article_count, 'article')}</Link>)}
                  {!coverage.data.entities.length && <p className="text-sm text-muted-foreground">No entities.</p>}
                </div>
                <ul className="flex flex-col gap-0.5">
                  {coverage.data.countries.map(country => <li key={`${country.role}-${country.country_code}`} className="text-sm text-foreground">{`${country.country_code} · ${country.role} · ${plural(country.article_count, 'article')}`}</li>)}
                  {coverage.data.languages.map(language => <li key={language.language} className="text-sm text-foreground">{`${language.language} · ${plural(language.article_count, 'article')}`}</li>)}
                </ul>
              </>
            )}
          </GlassPanel>

          <GlassPanel className="flex flex-col gap-2">
            <h3 className="text-sm font-semibold text-foreground">Timing in shared stories</h3>
            {timing.isPending && <p className="text-sm text-muted-foreground">Loading timing…</p>}
            {timing.isError && <p className="error text-sm text-destructive">Could not load timing.</p>}
            {timing.data && (timing.data.stories === 0
              ? <p className="text-sm text-muted-foreground">No stories shared with another source in this window.</p>
              : (
                <>
                  <p className="text-sm text-foreground">{`First in ${timing.data.first} of ${plural(timing.data.stories, 'shared story', 'shared stories')}`}</p>
                  {timing.data.median_minutes_behind !== null && timing.data.p90_minutes_behind !== null && (
                    <p className="text-sm text-muted-foreground">{`Otherwise behind the first article by a median of ${minutes(timing.data.median_minutes_behind)} (90th percentile ${minutes(timing.data.p90_minutes_behind)})`}</p>
                  )}
                </>
              ))}
            <p className="text-xs text-muted-foreground">Compares this source’s article with the earliest article in each story that another source also published. It describes timing, not quality.</p>
          </GlassPanel>
        </div>
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
                <span className="text-xs text-muted-foreground">{cluster.first === null ? 'Only this source' : cluster.first ? 'First to publish' : `${minutes(cluster.minutes_behind ?? 0)} behind the first article`}</span>
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
                <span className="text-xs text-muted-foreground">{when(article.published_at ?? article.first_discovered_at)}</span>
              </div>
            ))}
            {articles.hasNextPage && <div className="px-6 py-4"><button className={ghostButtonClass} disabled={articles.isFetchingNextPage} onClick={() => articles.fetchNextPage()}>{articles.isFetchingNextPage ? 'Loading…' : 'Load more articles'}</button></div>}
          </GlassPanel>
        </div>
      )}

      {data && (
        <GlassPanel className="overflow-hidden p-0">
          <section aria-label="Fetch history">
            <h3 className="px-6 pt-4 text-sm font-semibold text-foreground">Fetch history</h3>
            {fetches.isPending && <Note>Loading fetches…</Note>}
            {fetches.isError && <Note error>Could not load fetches.</Note>}
            {fetches.data && !fetchItems.length && <Note>No fetch attempts.</Note>}
            {fetchItems.map(fetch => (
              <article key={fetch.id} className="grid grid-cols-1 gap-1 border-t border-border px-6 py-3 sm:grid-cols-3">
                <strong className="text-sm font-semibold text-foreground">{fetch.status}</strong>
                <span className="text-sm text-muted-foreground">{when(fetch.started_at)}</span>
                <span className="text-sm text-muted-foreground">{fetch.new_article_count} new / {fetch.entry_count} entries</span>
                {fetch.error_message && <span className="error text-sm text-destructive sm:col-span-3">{fetch.error_category}: {fetch.error_message}</span>}
              </article>
            ))}
            {fetches.hasNextPage && <div className="px-6 py-4"><button className={ghostButtonClass} disabled={fetches.isFetchingNextPage} onClick={() => fetches.fetchNextPage()}>{fetches.isFetchingNextPage ? 'Loading…' : 'Load more fetches'}</button></div>}
          </section>
        </GlassPanel>
      )}
    </div>
  )
}

export default function SourceDetailPage() {
  return <Suspense fallback={null}><SourceContent /></Suspense>
}
