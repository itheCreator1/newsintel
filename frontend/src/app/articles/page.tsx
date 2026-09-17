'use client'

import { useMutation, useQuery, useQueryClient, useInfiniteQuery } from '@tanstack/react-query'
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useEffect } from 'react'
import { api } from '../../lib/api'
import { clusterHref, parseHref, queryFromState, refine, stateFromQuery, toHref, type ListField } from '../../lib/investigation'

function ArticlesContent() {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const client = useQueryClient()

  const feedId = searchParams.get('feed') ?? undefined
  const selectedId = searchParams.get('article') ?? undefined
  const from = searchParams.get('from') ?? ''
  const currentHref = toHref(pathname, searchParams)

  const feeds = useInfiniteQuery({ queryKey: ['feeds'], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam }) => api.feeds(pageParam), getNextPageParam: page => page.next_cursor ?? undefined })
  const articles = useInfiniteQuery({ queryKey: ['articles', feedId], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam }) => api.articles(feedId, pageParam), getNextPageParam: page => page.next_cursor ?? undefined })
  const feedItems = feeds.data?.pages.flatMap(page => page.items) ?? []
  const articleItems = articles.data?.pages.flatMap(page => page.items) ?? []
  const detail = useQuery({ queryKey: ['article', selectedId], queryFn: () => api.article(selectedId!), enabled: Boolean(selectedId), refetchInterval: 5000 })
  const annotations = useQuery({ queryKey: ['article-annotations', selectedId], queryFn: () => api.articleAnnotations(selectedId!), enabled: Boolean(selectedId), refetchInterval: 5000 })
  const process = useMutation({ mutationFn: (mode: 'full_text' | 'full_text_html') => api.processArticle(selectedId!, mode), onSuccess: () => { client.invalidateQueries({ queryKey: ['article', selectedId] }); client.invalidateQueries({ queryKey: ['jobs'] }) } })
  const reprocess = useMutation({ mutationFn: () => api.reprocessArticle(selectedId!), onSuccess: () => { client.invalidateQueries({ queryKey: ['article-annotations', selectedId] }); client.invalidateQueries({ queryKey: ['nlp-status'] }) } })
  const mentionedCountries = annotations.data?.countries.filter(item => item.role === 'mentioned') ?? []
  const primaryCountries = annotations.data?.countries.filter(item => item.role === 'primary') ?? []

  useEffect(() => { process.reset(); reprocess.reset() /* eslint-disable-line react-hooks/exhaustive-deps */ }, [selectedId])

  function setQuery(values: Record<string, string | undefined>) {
    const next = new URLSearchParams(searchParams.toString())
    for (const [key, value] of Object.entries(values)) { if (value === undefined) next.delete(key); else next.set(key, value) }
    router.replace(toHref(pathname, next))
  }

  // `from` can point back to a search, a story cluster, or the entity graph; label the return link for
  // whichever origin it actually is instead of always saying "search".
  const backLabel = from.startsWith('/clusters/') ? 'Back to story' : from.startsWith('/graph') ? 'Back to graph' : 'Back to search'

  function refinedSearchHref(field: ListField, value: string) {
    const origin = stateFromQuery(from.startsWith('/search') ? parseHref(from) : new URLSearchParams())
    return toHref('/search', queryFromState(refine(origin, field, value)))
  }

  return (
    <>
      <header>
        <div><p className="eyebrow">RSS archive</p><h2>Articles</h2></div>
        <div>
          <label className="filter">Source
            <select value={feedId ?? ''} onChange={event => setQuery({ feed: event.target.value || undefined, article: undefined })}>
              <option value="">All sources</option>
              {feedItems.map(feed => <option key={feed.id} value={feed.id}>{feed.name}</option>)}
            </select>
          </label>
          {feeds.hasNextPage && <button className="secondary" disabled={feeds.isFetchingNextPage} onClick={() => feeds.fetchNextPage()}>Load more sources</button>}
        </div>
      </header>
      <div className="article-layout">
        <section className="article-list panel">
          {articles.isPending && <p className="muted">Loading articles…</p>}
          {!articles.isPending && articles.isError && <p className="error">Could not load articles.</p>}
          {!articles.isPending && !articles.isError && !articleItems.length && <p className="muted">No collected articles.</p>}
          {articleItems.map(article => (
            <button key={article.id} className="article-row" onClick={() => setQuery({ article: article.id })}>
              <strong>{article.title}</strong>
              <span>{new Date(article.first_discovered_at).toLocaleString()}</span>
              <small>{article.provenance.map(p => p.feed_name).join(', ')}</small>
            </button>
          ))}
          {articles.hasNextPage && <button className="secondary" disabled={articles.isFetchingNextPage} onClick={() => articles.fetchNextPage()}>Load more articles</button>}
        </section>
        <section className="panel detail">
          {!selectedId && <p className="muted">Select an article to inspect its content and feed record.</p>}
          {selectedId && detail.isPending && <p className="muted">Loading article…</p>}
          {selectedId && !detail.isPending && detail.isError && <p className="error">Could not load article details.</p>}
          {selectedId && detail.data && (
            <>
              {from && <Link href={from}>{backLabel}</Link>}
              <p className="eyebrow">Article detail</p>
              <h3>{detail.data.title}</h3>
              <a href={detail.data.original_url} target="_blank" rel="noopener noreferrer">Open original article</a>
              <div className="actions processing-actions">
                <button disabled={process.isPending} onClick={() => process.mutate('full_text')}>{detail.data.content ? 'Refresh text' : 'Fetch text'}</button>
                <button className="secondary" disabled={process.isPending} onClick={() => process.mutate('full_text_html')}>Fetch and retain HTML</button>
              </div>
              {process.isPending && <p className="muted">Scheduling processing…</p>}
              {process.isSuccess && <p className="success">Processing scheduled.</p>}
              {process.error && <p className="error">Could not schedule processing.</p>}
              {detail.data.content ? (
                <article className="readable">
                  <p className="eyebrow">Readable text</p>
                  <p className="muted">Extracted {new Date(detail.data.content.extracted_at).toLocaleString()} · {detail.data.content.change_count} changes · HTML {detail.data.content.html_retained ? 'retained' : 'temporary'}</p>
                  <div className="article-text">{detail.data.content.text}</div>
                </article>
              ) : (
                <section className="processing">
                  <strong>Content status</strong>
                  <p className="muted">{detail.data.processing[0]?.status || 'Not processed'}</p>
                  {detail.data.processing[0]?.error_message && <p className="error">{detail.data.processing[0].error_category}: {detail.data.processing[0].error_message}</p>}
                </section>
              )}
              <section className="annotations">
                <div className="annotation-heading"><h4>Annotations</h4><button className="secondary" disabled={reprocess.isPending} onClick={() => reprocess.mutate()}>Reprocess annotations</button></div>
                {annotations.isPending && <p className="muted">Loading annotations…</p>}
                {!annotations.isPending && annotations.isError && <p className="error">Could not load annotations.</p>}
                {!annotations.isPending && !annotations.isError && annotations.data && (
                  <>
                    {annotations.data.language
                      ? <p>Detected language: {annotations.data.language.language} <span className="muted">({Math.round(annotations.data.language.confidence * 100)}% confidence)</span></p>
                      : <p className="muted">Detected language is not available.</p>}
                    <p>Source countries: {annotations.data.source_countries.length
                      ? annotations.data.source_countries.map((country, index) => (
                        <span key={country}>{index ? ', ' : ''}<Link className="annotation-link" aria-label={`Filter by source country ${country}`} href={refinedSearchHref('source_country', country)}>{country}</Link></span>
                      ))
                      : 'None'}</p>
                    <p>Mentioned countries: {mentionedCountries.length
                      ? mentionedCountries.map((item, index) => (
                        <span key={item.country_code}>{index ? ', ' : ''}<Link className="annotation-link" aria-label={`Filter by mentioned country ${item.country_code}`} href={refinedSearchHref('mentioned_country', item.country_code)}>{item.country_code}</Link></span>
                      ))
                      : 'None'}</p>
                    <p>Primary story country: {primaryCountries.length
                      ? primaryCountries.map((item, index) => (
                        <span key={item.country_code}>{index ? ', ' : ''}<Link className="annotation-link" aria-label={`Filter by story country ${item.country_code}`} href={refinedSearchHref('story_country', item.country_code)}>{item.country_code}</Link>{item.inferred ? ' (inferred)' : ''}</span>
                      ))
                      : 'Not inferred'}</p>
                    {annotations.data.keywords.length > 0 && (
                      <div className="annotation-group"><strong>Keywords</strong>{annotations.data.keywords.map(keyword => (
                        <Link key={keyword.id} className="annotation-link" href={refinedSearchHref('keyword_id', keyword.id)}>{keyword.text}</Link>
                      ))}</div>
                    )}
                    {annotations.data.entities.length > 0 && (
                      <div className="annotation-group"><strong>Entities</strong>{annotations.data.entities.map(entity => (
                        <Link key={entity.id} className="annotation-link" href={refinedSearchHref('entity_id', entity.id)}>{entity.text} ({entity.entity_type})</Link>
                      ))}</div>
                    )}
                    <div className="processor-list">
                      {annotations.data.processors.map(outcome => (
                        <p key={outcome.processor} className={outcome.status === 'failed' ? 'error' : 'muted'}>
                          {outcome.processor} · {outcome.completed_generation < outcome.requested_generation && 'stale · '}{outcome.status} · generation {outcome.completed_generation}/{outcome.requested_generation} · processor {outcome.processor_version}
                          {outcome.model_version && <span> · model {outcome.model_version}</span>}
                          {outcome.detail && <span> · {outcome.detail}</span>}
                        </p>
                      ))}
                      {annotations.data.capabilities.filter(item => item.state !== 'available').map(capability => (
                        <p key={capability.name} className="muted">{capability.name} · {capability.state}{capability.detail && <> · {capability.detail}</>}</p>
                      ))}
                    </div>
                  </>
                )}
                {reprocess.isSuccess && <p className="success">Annotation processing scheduled.</p>}
                {reprocess.isError && <p className="error">Could not schedule annotation processing.</p>}
              </section>
              <section className="story">
                <h4>Story</h4>
                {detail.data.story_cluster ? (
                  <>
                    <p>{detail.data.story_cluster.article_count} articles from {detail.data.story_cluster.source_count} sources</p>
                    {(detail.data.related ?? []).length > 0 && (
                      <div className="annotation-group"><strong>Related articles</strong>{(detail.data.related ?? []).map(related => (
                        <Link key={related.article_id} className="annotation-link" href={toHref(pathname, (() => { const next = new URLSearchParams(searchParams.toString()); next.set('article', related.article_id); return next })())}>{related.title}</Link>
                      ))}</div>
                    )}
                    <div className="actions">
                      <Link className="secondary" href={clusterHref(detail.data.story_cluster.id, currentHref)}>Open full cluster</Link>
                      <Link className="annotation-link" aria-label="Filter by story" href={refinedSearchHref('story_cluster_id', detail.data.story_cluster.id)}>Filter by story</Link>
                    </div>
                  </>
                ) : <p className="muted">Not part of a detected story.</p>}
              </section>
              <dl>
                <dt>Published</dt><dd>{detail.data.published_at ? new Date(detail.data.published_at).toLocaleString() : 'Not supplied'}</dd>
                <dt>First discovered</dt><dd>{new Date(detail.data.first_discovered_at).toLocaleString()}</dd>
              </dl>
              {detail.data.provenance.map(source => (
                <article key={source.feed_id} className="provenance">
                  <Link className="annotation-link" aria-label={`Filter by source ${source.feed_name}`} href={refinedSearchHref('source_id', source.feed_id)}><strong>{source.feed_name}</strong></Link>
                  <p>{source.description || 'No RSS description.'}</p>
                </article>
              ))}
            </>
          )}
        </section>
      </div>
    </>
  )
}

export default function ArticlesPage() {
  return <Suspense fallback={null}><ArticlesContent /></Suspense>
}
