'use client'

import { useInfiniteQuery } from '@tanstack/react-query'
import Link from 'next/link'
import { api, ApiError } from '../lib/api'
import { clusterHref, entityHref, toHref } from '../lib/investigation'
import { chipClass, ghostButtonClass } from '../lib/ui-classes'
import { cn, plural } from '../lib/utils'
import { glassPanelClassName } from './GlassPanel'

type Filters = Record<string, string | string[] | undefined>
const when = (value: string | null | undefined) => value ? new Date(value).toLocaleDateString() : '—'

function failure(reason: unknown) {
  if (reason instanceof ApiError) {
    if (reason.status === 404) return 'One of these entities no longer exists.'
    if (reason.status === 503) return 'Relationship evidence is temporarily unavailable.'
    const detail = reason.detail
    if (reason.status === 409 && detail && typeof detail === 'object' && 'code' in detail && detail.code === 'search_upgrade_required') return 'Search upgrade required. Rebuild the search index to use the entity graph.'
  }
  return 'Could not load relationship evidence.'
}

/** The articles and stories behind one graph edge, under the same filters the graph was drawn with. */
export function EdgeEvidencePanel({ source, target, filters, returnHref }: { source: string; target: string; filters: Filters; returnHref: string }) {
  const params = { ...filters, source, target }
  const evidence = useInfiniteQuery({
    queryKey: ['edge-evidence', params], initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => api.edgeEvidence(params, pageParam), getNextPageParam: page => page.next_cursor ?? undefined, retry: false,
  })
  const head = evidence.data?.pages[0]
  const articles = evidence.data?.pages.flatMap(page => page.articles) ?? []
  const missing = evidence.data?.pages.reduce((total, page) => total + page.missing_from_archive, 0) ?? 0

  return (
    <aside aria-label="Relationship evidence" className={cn(glassPanelClassName, 'flex flex-col gap-3')}>
      {evidence.isPending && <p className="text-sm text-muted-foreground">Loading evidence…</p>}
      {evidence.isError && <p role="alert" className="error text-sm text-destructive">{failure(evidence.error)}</p>}
      {head && (
        <>
          <h3 className="text-lg font-semibold text-foreground">{head.source.text} and {head.target.text}</h3>
          <p className="text-sm text-muted-foreground">{head.meaning}</p>
          <p className="text-sm text-foreground">{`${plural(head.article_count, 'article')} · ${head.cluster_count_estimated ? 'about ' : ''}${plural(head.cluster_count, 'story', 'stories')}${head.cluster_count_estimated ? ' (estimated)' : ''}`}</p>
          <p className="text-sm text-muted-foreground">First seen {when(head.first_at)} · Latest {when(head.last_at)}</p>
          <div className="flex flex-wrap gap-2">
            {[head.source, head.target].map(entity => <Link key={entity.id} className={chipClass} href={entityHref(entity.id)}>Open dossier for {entity.text}</Link>)}
          </div>
          {!head.article_count && <p className="text-sm text-muted-foreground">No articles contain both entities under these filters.</p>}
          {missing > 0 && <p className="text-sm text-muted-foreground">{`${plural(missing, 'matching article')} ${missing === 1 ? 'is' : 'are'} no longer in the archive; rebuild search to refresh the index.`}</p>}
          {head.clusters.length > 0 && (
            <div>
              <strong className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Stories</strong>
              {head.cluster_count > head.clusters.length && <p className="mt-1 text-xs text-muted-foreground">{`Showing the ${head.clusters.length} stories with the most of these articles.`}</p>}
              <ul className="mt-2 flex flex-col gap-2">
                {head.clusters.map(story => (
                  <li key={story.id} className="flex flex-col gap-0.5">
                    <Link className="text-sm text-primary underline-offset-4 hover:underline" href={clusterHref(story.id, returnHref)}>{story.representative_article?.title ?? 'Story'}</Link>
                    <span className="text-xs text-muted-foreground">{`${story.edge_article_count} of these articles · ${plural(story.article_count, 'article')} from ${plural(story.source_count, 'source')}`}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {articles.length > 0 && (
            <div>
              <strong className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Articles</strong>
              <ul className="mt-2 flex flex-col gap-2">
                {articles.map(article => (
                  <li key={article.id} className="flex flex-col gap-0.5">
                    <Link className="text-sm text-primary underline-offset-4 hover:underline" href={toHref('/articles', new URLSearchParams({ article: article.id, from: returnHref }))}>{article.title}</Link>
                    <span className="text-xs text-muted-foreground">{when(article.published_at ?? article.first_discovered_at)}</span>
                  </li>
                ))}
              </ul>
              {evidence.hasNextPage && <button type="button" className={cn(ghostButtonClass, 'mt-3')} disabled={evidence.isFetchingNextPage} onClick={() => evidence.fetchNextPage()}>{evidence.isFetchingNextPage ? 'Loading…' : 'Load more articles'}</button>}
            </div>
          )}
        </>
      )}
    </aside>
  )
}
