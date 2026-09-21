'use client'

import { useInfiniteQuery } from '@tanstack/react-query'
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Suspense } from 'react'
import { api } from '../../lib/api'
import { GlassPanel } from '../../components/GlassPanel'
import { PageHeader } from '../../components/PageHeader'
import { WatchForm } from '../../components/WatchForm'
import { chipClass, ghostButtonClass } from '../../lib/ui-classes'
import { emptyInvestigation, parseHref, queryFromState, refine, stateFromQuery, toHref } from '../../lib/investigation'

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
  const header = cluster.data?.pages[0]
  const members = cluster.data?.pages.flatMap(page => page.members.items) ?? []
  const searchWithinStoryHref = (() => {
    const validFrom = from.startsWith('/search') ? from : ''
    const origin = stateFromQuery(validFrom ? parseHref(validFrom) : new URLSearchParams())
    return toHref('/search', queryFromState(id ? refine(origin, 'story_cluster_id', id) : origin))
  })()
  function openArticleHref(articleId: string) { return toHref('/articles', new URLSearchParams({ article: articleId, from: currentHref })) }

  return (
    <div className="flex flex-col gap-6 font-sans">
      <PageHeader eyebrow="Story" title="Cluster" />
      <GlassPanel className="overflow-hidden p-0">
        {cluster.isPending && <p className="px-6 py-4 text-sm text-muted-foreground">Loading story…</p>}
        {!cluster.isPending && cluster.isError && <p className="error px-6 py-4 text-sm text-destructive">Could not load this story.</p>}
        {!cluster.isPending && !cluster.isError && header && (
          <>
            <div className="flex flex-col gap-2 px-6 py-4">
              <h3 className="text-sm font-semibold text-foreground">{header.article_count} articles · {header.source_count} sources</h3>
              <p className="text-sm text-muted-foreground">
                {header.first_published_at && header.last_published_at
                  ? `${new Date(header.first_published_at).toLocaleString()} – ${new Date(header.last_published_at).toLocaleString()}`
                  : 'Publication dates are not available.'}
              </p>
              <Link className={chipClass} href={searchWithinStoryHref}>Search within this story</Link>
              <WatchForm key={id} className="" kind="cluster" state={refine(emptyInvestigation(), 'story_cluster_id', id)} defaultName={members[0]?.title ?? ''} label="Watch story" />
            </div>
            {members.map(member => (
              <article key={member.article_id} className="search-result border-border px-6 py-4 last:border-b-0 hover:bg-accent/40">
                <button className="result-open transition-colors" onClick={() => router.push(openArticleHref(member.article_id))}>
                  <strong className="text-[15px] font-semibold text-foreground">{member.title}</strong>
                  <span className="text-xs text-muted-foreground">{new Date(member.effective_date).toLocaleString()} · {member.feeds.map(feed => feed.name).join(', ')}</span>
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
