'use client'

import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Suspense } from 'react'
import { api } from '../../lib/api'
import type { StorySummary } from '../../lib/api-types'
import { GlassPanel } from '../../components/GlassPanel'
import { PageHeader } from '../../components/PageHeader'
import { TimelineChart } from '../../components/TimelineChart'
import { WatchButton } from '../../components/WatchForm'
import { chipClass, ghostButtonClass } from '../../lib/ui-classes'
import { brushRange, emptyInvestigation, entityHref, eventHref, parseHref, queryFromState, refine, sourceHref, stateFromQuery, toHref, type Investigation } from '../../lib/investigation'
import { ErrorNotice, LoadError } from '../../components/Feedback'
import { formatDateTime } from '../../lib/datetime'

/** How long after the story's first report a source followed: "First", "+45 min", "+2 h 30 min", "+1 d 1 h". */
function delayLabel(minutes: number): string {
  const total = Math.round(minutes)
  if (total <= 0) return 'First'
  const days = Math.floor(total / 1440), hours = Math.floor((total % 1440) / 60), rest = total % 60
  if (days) return `+${days} d${hours ? ` ${hours} h` : ''}`
  if (hours) return `+${hours} h${rest ? ` ${rest} min` : ''}`
  return `+${rest} min`
}

function StoryOverview({ summary, currentHref, onBrush }: { summary: StorySummary; currentHref: string; onBrush(range: { start: string; end: string }): void }) {
  const first = summary.sources[0]
  return (
    <>
      <div className="flex flex-col gap-3">
        {first && <p className="text-sm text-foreground">First reported by <Link className="underline" href={sourceHref(first.id, currentHref)}>{first.name}</Link> on {formatDateTime(first.first_reported_at)}</p>}
        {summary.event && <p className="text-sm text-muted-foreground">Part of the event <Link className="text-foreground underline" href={eventHref(summary.event.id, currentHref)}>{summary.event.headline ?? 'Untitled event'}</Link></p>}
        {summary.entities.length > 0 && (
          <div className="flex flex-wrap gap-2" aria-label="Story entities">
            {summary.entities.map(entity => <Link key={entity.id} className={chipClass} href={entityHref(entity.id)} aria-label={`${entity.display_name} (${entity.article_count})`} title={entity.entity_type}>{entity.display_name} <span className="text-muted-foreground">{entity.article_count}</span></Link>)}
          </div>
        )}
      </div>
      {summary.timeline.buckets.length > 0 && (
        <GlassPanel aria-labelledby="story-timeline-heading" className="p-4">
          <h3 id="story-timeline-heading" className="mb-2 text-sm font-semibold text-foreground">Articles by {summary.timeline.interval}</h3>
          <TimelineChart buckets={summary.timeline.buckets} interval={summary.timeline.interval} onSelect={onBrush} />
        </GlassPanel>
      )}
      {summary.sources.length > 0 && (
        <GlassPanel className="overflow-x-auto p-4">
          <table aria-label="Who reported it when" className="w-full text-left text-sm">
            <caption className="mb-2 text-left text-sm font-semibold text-foreground">Who reported it when</caption>
            <thead className="text-xs text-muted-foreground"><tr><th className="py-1 pr-4 font-medium">Source</th><th className="py-1 pr-4 font-medium">First article</th><th className="py-1 font-medium">After the first report</th></tr></thead>
            <tbody>
              {summary.sources.map(source => (
                <tr key={source.id} className="border-t border-border/60">
                  <td className="py-1.5 pr-4"><Link className="text-foreground underline" href={sourceHref(source.id, currentHref)}>{source.name}</Link></td>
                  <td className="py-1.5 pr-4 text-muted-foreground">{formatDateTime(source.first_reported_at)}</td>
                  <td className="py-1.5 text-muted-foreground">{delayLabel(source.minutes_behind)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </GlassPanel>
      )}
    </>
  )
}

function ClusterContent() {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const id = searchParams.get('id') ?? ''
  const from = searchParams.get('from') ?? ''
  const currentHref = toHref(pathname, searchParams)

  const cluster = useInfiniteQuery({
    queryKey: ['cluster', id], initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => api.cluster(id, pageParam), getNextPageParam: page => page.members.next_cursor ?? undefined, enabled: Boolean(id), retry: false,
  })
  const summary = useQuery({ queryKey: ['cluster-summary', id], queryFn: () => api.clusterSummary(id), enabled: Boolean(id), retry: false })
  const header = cluster.data?.pages[0]
  const members = cluster.data?.pages.flatMap(page => page.members.items) ?? []
  // Searching within the story keeps the investigation it was opened from.
  const withinStory = (() => {
    const validFrom = from.startsWith('/search') ? from : ''
    const origin = stateFromQuery(validFrom ? parseHref(validFrom) : new URLSearchParams())
    return id ? refine(origin, 'story_cluster_id', id) : origin
  })()
  const searchHref = (state: Investigation) => toHref('/search', queryFromState(state))
  function searchBrushed(range: { start: string; end: string }) { router.push(searchHref({ ...withinStory, ...brushRange(range.start, range.end) })) }
  function openArticleHref(articleId: string) { return toHref('/articles', new URLSearchParams({ article: articleId, from: currentHref })) }

  return (
    <div className="flex flex-col gap-6 font-sans">
      <PageHeader eyebrow="Story" title={summary.data?.title ?? 'Story'} description={header ? `${header.article_count} articles · ${header.source_count} sources` : undefined}>
        {header && (
          <div className="flex flex-wrap items-center gap-2">
            <Link className={chipClass} href={searchHref(withinStory)}>Search within this story</Link>
            <WatchButton key={id} kind="cluster" state={refine(emptyInvestigation(), 'story_cluster_id', id)} defaultName={summary.data?.title ?? members[0]?.title ?? ''} label="Watch story" />
          </div>
        )}
      </PageHeader>
      {summary.data && <StoryOverview summary={summary.data} currentHref={currentHref} onBrush={searchBrushed} />}
      {summary.isError && <ErrorNotice message="Could not load the story summary." onRetry={() => void summary.refetch()} retrying={summary.isFetching} />}
      <GlassPanel className="overflow-hidden p-0">
        {cluster.isPending && <p className="px-6 py-4 text-sm text-muted-foreground">Loading story…</p>}
        {!cluster.isPending && cluster.isError && <LoadError className="px-6 py-4" query={cluster} message="Could not load this story." />}
        {!cluster.isPending && !cluster.isError && header && (
          <>
            <p className="px-6 pt-4 text-sm text-muted-foreground">
              {header.first_published_at && header.last_published_at
                ? `${formatDateTime(header.first_published_at)} – ${formatDateTime(header.last_published_at)}`
                : 'Publication dates are not available.'}
            </p>
            {members.map(member => (
              <article key={member.article_id} className="search-result border-border px-6 py-4 last:border-b-0 hover:bg-accent/40">
                <button className="result-open transition-colors" onClick={() => router.push(openArticleHref(member.article_id))}>
                  <strong className="text-[15px] font-semibold text-foreground">{member.title}</strong>
                  <span className="text-xs text-muted-foreground">{formatDateTime(member.effective_date)} · {member.feeds.map(feed => feed.name).join(', ')}</span>
                </button>
              </article>
            ))}
            {cluster.hasNextPage && <div className="px-6 py-4"><button className={ghostButtonClass} disabled={cluster.isFetchingNextPage} onClick={() => cluster.fetchNextPage()}>{cluster.isFetchingNextPage ? 'Loading…' : 'Load more'}</button></div>}
          </>
        )}
      </GlassPanel>
    </div>
  )
}

export default function ClustersPage() {
  return <Suspense fallback={null}><ClusterContent /></Suspense>
}
