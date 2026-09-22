'use client'

import { useQuery } from '@tanstack/react-query'
import Link from 'next/link'
import { api, ApiError } from '../lib/api'

/** Articles worded like this one, outside its story: text similarity, not story membership. */
export function RelatedCoveragePanel({ articleId, hrefFor }: { articleId: string; hrefFor: (id: string) => string }) {
  const related = useQuery({ queryKey: ['article-related', articleId], queryFn: () => api.relatedArticles(articleId) })
  const items = related.data?.items ?? []
  const skipped = related.data?.skipped_stale ?? 0
  return (
    <section className="related-coverage flex flex-col gap-2 border-t border-border pt-4" aria-labelledby="related-coverage-heading">
      <h4 id="related-coverage-heading" className="text-sm font-semibold text-foreground">Related coverage</h4>
      <p className="text-sm text-muted-foreground">Other articles with similar wording, outside this article&apos;s story. Similar wording is not a confirmed connection.</p>
      {related.isPending && <p className="text-sm text-muted-foreground">Finding related coverage…</p>}
      {related.isError && <p className="error text-sm text-destructive">{related.error instanceof ApiError && related.error.status === 409 ? related.error.message : 'Related coverage is unavailable right now.'}</p>}
      {related.data && !items.length && <p className="text-sm text-muted-foreground">No other coverage with similar wording.</p>}
      {items.length > 0 && (
        <ul className="flex flex-col gap-1">
          {items.map(({ article }) => (
            <li key={article.id} className="flex flex-wrap items-baseline gap-2">
              <Link className="text-sm text-primary underline-offset-4 hover:underline" href={hrefFor(article.id)}>{article.title}</Link>
              <span className="text-xs text-muted-foreground">{article.provenance.map(source => source.feed_name).join(', ')}</span>
            </li>
          ))}
        </ul>
      )}
      {skipped > 0 && <p className="text-xs text-muted-foreground">{skipped} {skipped === 1 ? 'match was' : 'matches were'} skipped because the search index is catching up.</p>}
    </section>
  )
}
