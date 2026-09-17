'use client'

import { useInfiniteQuery } from '@tanstack/react-query'
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Suspense } from 'react'
import { api } from '../../lib/api'
import { parseHref, queryFromState, refine, stateFromQuery, toHref } from '../../lib/investigation'

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
    <>
      <header><div><p className="eyebrow">Story</p><h2>Cluster</h2></div></header>
      <section className="panel">
        {cluster.isPending && <p className="muted">Loading story…</p>}
        {!cluster.isPending && cluster.isError && <p className="error">Could not load this story.</p>}
        {!cluster.isPending && !cluster.isError && header && (
          <>
            <h3>{header.article_count} articles · {header.source_count} sources</h3>
            <p className="muted">
              {header.first_published_at && header.last_published_at
                ? `${new Date(header.first_published_at).toLocaleString()} – ${new Date(header.last_published_at).toLocaleString()}`
                : 'Publication dates are not available.'}
            </p>
            <Link className="annotation-link" href={searchWithinStoryHref}>Search within this story</Link>
            {members.map(member => (
              <article key={member.article_id} className="search-result">
                <button className="result-open" onClick={() => router.push(openArticleHref(member.article_id))}>
                  <strong>{member.title}</strong>
                  <span>{new Date(member.effective_date).toLocaleString()} · {member.feeds.map(feed => feed.name).join(', ')}</span>
                </button>
              </article>
            ))}
            {cluster.hasNextPage && <button className="secondary" disabled={cluster.isFetchingNextPage} onClick={() => cluster.fetchNextPage()}>{cluster.isFetchingNextPage ? 'Loading…' : 'Load more'}</button>}
          </>
        )}
      </section>
    </>
  )
}

export default function ClustersPage() {
  return <Suspense fallback={null}><ClusterContent /></Suspense>
}
