'use client'

import { useMutation, useQuery, useQueryClient, useInfiniteQuery } from '@tanstack/react-query'
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../lib/api'
import { clusterHref, entityHref, parseHref, queryFromState, refine, sourceHref, stateFromQuery, toHref, type ListField } from '../../lib/investigation'
import { ArticleEntityLink } from '../../components/ArticleEntityLink'
import { ArticleReader } from '../../components/ArticleReader'
import { GlassPanel } from '../../components/GlassPanel'
import { PageHeader } from '../../components/PageHeader'
import { RelatedCoveragePanel } from '../../components/RelatedCoveragePanel'
import { highlightRanges, type ReaderRange } from '../../lib/reader'
import { chipClass, fieldClass, ghostButtonClass, labelClass } from '../../lib/ui-classes'
import { cn } from '../../lib/utils'
import { LoadError } from '../../components/Feedback'

const HIGHLIGHTS_KEY = 'newsintel.reader.highlights'

function storedHighlights() {
  try { return localStorage.getItem(HIGHLIGHTS_KEY) !== 'off' } catch { return true }
}

function ArticlesContent() {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const client = useQueryClient()

  const feedId = searchParams.get('feed') ?? undefined
  const selectedId = searchParams.get('article') ?? undefined
  const from = searchParams.get('from') ?? ''
  // Focus mode hides the list, so it only means something while an article is open.
  const focused = searchParams.get('focus') === '1' && Boolean(selectedId)
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
  const selectedIndex = articleItems.findIndex(item => item.id === selectedId)
  const hasNext = selectedIndex < articleItems.length - 1 || Boolean(articles.hasNextPage)
  const [highlights, setHighlights] = useState(storedHighlights)
  const text = detail.data?.content?.text ?? null
  // Both queries refetch every five seconds; unchanged data keeps its identity, so this does not recompute.
  const ranges = useMemo(() => highlights && text && annotations.data ? highlightRanges(text, annotations.data) : [], [highlights, text, annotations.data])
  const listRef = useRef<HTMLElement>(null)
  const readerRef = useRef<HTMLElement>(null)

  useEffect(() => { process.reset(); reprocess.reset() /* eslint-disable-line react-hooks/exhaustive-deps */ }, [selectedId])

  function setQuery(values: Record<string, string | undefined>) {
    const next = new URLSearchParams(searchParams.toString())
    for (const [key, value] of Object.entries(values)) { if (value === undefined) next.delete(key); else next.set(key, value) }
    router.replace(toHref(pathname, next))
  }

  // `from` can point back to a search, a story cluster, an event, or the entity graph; label the return link for
  // whichever origin it actually is instead of always saying "search".
  const backLabel = from.startsWith('/clusters/') ? 'Back to story' : from.startsWith('/graph') ? 'Back to graph' : from.startsWith('/entities') ? 'Back to entity' : from.startsWith('/events') ? 'Back to event' : from.startsWith('/sources/detail') ? 'Back to source' : from.startsWith('/compare') ? 'Back to comparison' : 'Back to search'

  function refinedSearchHref(field: ListField, value: string) {
    const origin = stateFromQuery(from.startsWith('/search') ? parseHref(from) : new URLSearchParams())
    return toHref('/search', queryFromState(refine(origin, field, value)))
  }

  async function step(delta: 1 | -1) {
    const target = articleItems[selectedIndex + delta]
    if (target) return setQuery({ article: target.id })
    if (delta === 1 && articles.hasNextPage && !articles.isFetchingNextPage) {
      const next = (await articles.fetchNextPage()).data?.pages.at(-1)?.items[0]
      if (next) setQuery({ article: next.id })
    }
  }

  function toggleHighlights() {
    try { localStorage.setItem(HIGHLIGHTS_KEY, highlights ? 'off' : 'on') } catch { /* a preference only; the toggle still works for this visit */ }
    setHighlights(!highlights)
  }

  function rangeHref(range: ReaderRange) {
    return range.kind === 'entity' ? entityHref(range.id) : refinedSearchHref('keyword_id', range.id)
  }

  // Start each article at its top. The panel scrolls itself beside the list; in focus mode and on
  // narrow screens the page scrolls instead, so bring the panel back only when its top is off screen.
  useEffect(() => {
    const panel = readerRef.current
    if (!panel || !selectedId) return
    panel.scrollTo?.({ top: 0 })
    const top = panel.getBoundingClientRect().top
    if (top < 0 || top > window.innerHeight) panel.scrollIntoView?.({ block: 'start' })
    listRef.current?.querySelector('[aria-current="true"]')?.scrollIntoView?.({ block: 'nearest' })
  }, [selectedId])

  // Re-subscribed on every render so the handler always sees the current list, selection and URL.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.metaKey || event.ctrlKey || event.altKey) return
      if (event.target instanceof Element && event.target.closest('input, select, textarea, [contenteditable="true"]')) return
      if (event.key === 'j') step(1)
      else if (event.key === 'k') step(-1)
      else if (event.key === 'f' && selectedId) setQuery({ focus: focused ? undefined : '1' })
      else if (event.key === 'Escape' && focused) setQuery({ focus: undefined })
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  })

  function articleHref(id: string) {
    const next = new URLSearchParams(searchParams.toString())
    next.set('article', id)
    return toHref(pathname, next)
  }

  return (
    <div className="flex flex-col gap-6 font-sans">
      <PageHeader eyebrow="RSS archive" title="Articles">
        <div className="flex flex-wrap items-end gap-3">
          <label className={labelClass}>Source
            <select className={cn(fieldClass, 'mt-1')} value={feedId ?? ''} onChange={event => setQuery({ feed: event.target.value || undefined, article: undefined })}>
              <option value="">All sources</option>
              {feedItems.map(feed => <option key={feed.id} value={feed.id}>{feed.name}</option>)}
            </select>
          </label>
          {feeds.hasNextPage && <button type="button" className={ghostButtonClass} disabled={feeds.isFetchingNextPage} onClick={() => feeds.fetchNextPage()}>Load more sources</button>}
        </div>
      </PageHeader>

      <div className={cn('grid items-start gap-6', focused ? 'mx-auto w-full max-w-4xl' : 'lg:grid-cols-[minmax(280px,0.8fr)_minmax(0,1.2fr)]')}>
        {!focused && <GlassPanel ref={listRef} className="overflow-hidden p-0">
          {articles.isPending && <p className="text-sm text-muted-foreground px-6 py-4">Loading articles…</p>}
          {!articles.isPending && articles.isError && <LoadError className="px-6 py-4" query={articles} message="Could not load articles." />}
          {!articles.isPending && !articles.isError && !articleItems.length && <p className="text-sm text-muted-foreground px-6 py-4">No collected articles.</p>}
          {articleItems.map(article => (
            <button key={article.id} aria-current={article.id === selectedId ? 'true' : undefined} className="article-row flex w-full flex-col gap-1 border-t border-border px-6 py-4 text-left transition-colors first:border-t-0 hover:bg-accent/40 aria-[current=true]:bg-accent aria-[current=true]:shadow-[inset_3px_0_0_0_var(--primary)]" onClick={() => setQuery({ article: article.id })}>
              <strong className="text-[15px] font-semibold text-foreground">{article.title}</strong>
              <span className="text-xs text-muted-foreground">{new Date(article.first_discovered_at).toLocaleString()}</span>
              <small className="text-xs text-muted-foreground">{article.provenance.map(p => p.feed_name).join(', ')}</small>
            </button>
          ))}
          {articles.hasNextPage && <div className="border-t border-border px-6 py-4"><button type="button" className={ghostButtonClass} disabled={articles.isFetchingNextPage} onClick={() => articles.fetchNextPage()}>Load more articles</button></div>}
        </GlassPanel>}

        <GlassPanel ref={readerRef} className={cn('flex flex-col gap-3', !focused && 'lg:sticky lg:top-8 lg:max-h-[calc(100dvh-4rem)] lg:overflow-y-auto')}>
          {selectedId && (
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" className={ghostButtonClass} title="Previous article (k)" disabled={selectedIndex <= 0} onClick={() => step(-1)}>Previous</button>
              <button type="button" className={ghostButtonClass} title="Next article (j)" disabled={!hasNext || articles.isFetchingNextPage} onClick={() => step(1)}>Next</button>
              <button type="button" className={ghostButtonClass} title="Focus mode (f)" aria-pressed={focused} onClick={() => setQuery({ focus: focused ? undefined : '1' })}>{focused ? 'Exit focus' : 'Focus'}</button>
              <button type="button" className={cn(ghostButtonClass, highlights && 'border-ring text-foreground')} title="Mark entities and keywords in the text" aria-pressed={highlights} onClick={toggleHighlights}>Highlights</button>
              {from && <Link className="ml-auto text-sm text-primary underline-offset-4 hover:underline" href={from}>{backLabel}</Link>}
            </div>
          )}
          {!selectedId && <p className="text-sm text-muted-foreground">Select an article to inspect its content and feed record.</p>}
          {selectedId && detail.isPending && <p className="text-sm text-muted-foreground">Loading article…</p>}
          {selectedId && !detail.isPending && detail.isError && <p className="error text-sm text-destructive">Could not load article details.</p>}
          {selectedId && detail.data && (
            <>
              <ArticleReader
                title={detail.data.title}
                text={text}
                summary={detail.data.provenance.find(source => source.description)?.description}
                date={detail.data.published_at ?? detail.data.first_discovered_at}
                sources={[...new Set(detail.data.provenance.map(source => source.feed_name))]}
                ranges={ranges}
                hrefFor={rangeHref}
              />
              <a className="text-sm text-primary underline-offset-4 hover:underline" href={detail.data.original_url} target="_blank" rel="noopener noreferrer">Open original article</a>
              <div className="flex gap-2">
                <button type="button" className={cn(fieldClass, 'w-auto bg-primary text-primary-foreground')} disabled={process.isPending} onClick={() => process.mutate('full_text')}>{detail.data.content ? 'Refresh text' : 'Fetch text'}</button>
                <button type="button" className={ghostButtonClass} disabled={process.isPending} onClick={() => process.mutate('full_text_html')}>Fetch and retain HTML</button>
              </div>
              {process.isPending && <p className="text-sm text-muted-foreground">Scheduling processing…</p>}
              {process.isSuccess && <p className="text-sm text-primary">Processing scheduled.</p>}
              {process.error && <p className="error text-sm text-destructive">Could not schedule processing.</p>}
              {detail.data.content ? (
                <p className="text-sm text-muted-foreground">Extracted {new Date(detail.data.content.extracted_at).toLocaleString()} · {detail.data.content.change_count} changes · HTML {detail.data.content.html_retained ? 'retained' : 'temporary'}</p>
              ) : (
                <section className="processing flex flex-col gap-1 border-t border-border pt-4">
                  <strong className="text-sm font-semibold text-foreground">Content status</strong>
                  <p className="text-sm text-muted-foreground">{detail.data.processing[0]?.status || 'Not processed'}</p>
                  {detail.data.processing[0]?.error_message && <p className="error text-sm text-destructive">{detail.data.processing[0].error_category}: {detail.data.processing[0].error_message}</p>}
                </section>
              )}
              <section className="annotations flex flex-col gap-2 border-t border-border pt-4">
                <div className="flex items-center justify-between gap-3"><h4 className="text-sm font-semibold text-foreground">Annotations</h4><button type="button" className={ghostButtonClass} disabled={reprocess.isPending} onClick={() => reprocess.mutate()}>Reprocess annotations</button></div>
                {annotations.isPending && <p className="text-sm text-muted-foreground">Loading annotations…</p>}
                {!annotations.isPending && annotations.isError && <LoadError query={annotations} message="Could not load annotations." />}
                {!annotations.isPending && !annotations.isError && annotations.data && (
                  <>
                    {annotations.data.language
                      ? <p className="text-sm text-foreground">Detected language: {annotations.data.language.language} <span className="text-muted-foreground">({Math.round(annotations.data.language.confidence * 100)}% confidence)</span></p>
                      : <p className="text-sm text-muted-foreground">Detected language is not available.</p>}
                    <p className="text-sm text-foreground">Source countries: {annotations.data.source_countries.length
                      ? annotations.data.source_countries.map((country, index) => (
                        <span key={country}>{index ? ', ' : ''}<Link className={chipClass} aria-label={`Filter by source country ${country}`} href={refinedSearchHref('source_country', country)}>{country}</Link></span>
                      ))
                      : 'None'}</p>
                    <p className="text-sm text-foreground">Mentioned countries: {mentionedCountries.length
                      ? mentionedCountries.map((item, index) => (
                        <span key={item.country_code}>{index ? ', ' : ''}<Link className={chipClass} aria-label={`Filter by mentioned country ${item.country_code}`} href={refinedSearchHref('mentioned_country', item.country_code)}>{item.country_code}</Link></span>
                      ))
                      : 'None'}</p>
                    <p className="text-sm text-foreground">Primary story country: {primaryCountries.length
                      ? primaryCountries.map((item, index) => (
                        <span key={item.country_code}>{index ? ', ' : ''}<Link className={chipClass} aria-label={`Filter by story country ${item.country_code}`} href={refinedSearchHref('story_country', item.country_code)}>{item.country_code}</Link>{item.inferred ? ' (inferred)' : ''}</span>
                      ))
                      : 'Not inferred'}</p>
                    {annotations.data.keywords.length > 0 && (
                      <div className="annotation-group flex flex-wrap items-center gap-2"><strong className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Keywords</strong>{annotations.data.keywords.map(keyword => (
                        <Link key={keyword.id} className={chipClass} href={refinedSearchHref('keyword_id', keyword.id)}>{keyword.text}</Link>
                      ))}</div>
                    )}
                    {annotations.data.entities.length > 0 && (
                      <div className="annotation-group flex flex-wrap items-center gap-2"><strong className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Entities</strong>{annotations.data.entities.map(entity => (
                        <span key={entity.id} className="inline-flex items-center gap-1">
                          <Link className={chipClass} href={refinedSearchHref('entity_id', entity.id)}>{entity.text} ({entity.entity_type})</Link>
                          <Link className={chipClass} href={entityHref(entity.id)} aria-label={`Open dossier for ${entity.text}`}>Dossier</Link>
                        </span>
                      ))}</div>
                    )}
                    {selectedId && <ArticleEntityLink key={selectedId} articleId={selectedId} entities={annotations.data.entities} />}
                    <div className="flex flex-col gap-1">
                      {annotations.data.processors.map(outcome => (
                        <p key={outcome.processor} className={cn('text-sm', outcome.status === 'failed' ? 'error text-destructive' : 'text-muted-foreground')}>
                          {outcome.processor} · {outcome.completed_generation < outcome.requested_generation && 'stale · '}{outcome.status} · generation {outcome.completed_generation}/{outcome.requested_generation} · processor {outcome.processor_version}
                          {outcome.model_version && <span> · model {outcome.model_version}</span>}
                          {outcome.detail && <span> · {outcome.detail}</span>}
                        </p>
                      ))}
                      {annotations.data.capabilities.filter(item => item.state !== 'available').map(capability => (
                        <p key={capability.name} className="text-sm text-muted-foreground">{capability.name} · {capability.state}{capability.detail && <> · {capability.detail}</>}</p>
                      ))}
                    </div>
                  </>
                )}
                {reprocess.isSuccess && <p className="text-sm text-primary">Annotation processing scheduled.</p>}
                {reprocess.isError && <p className="error text-sm text-destructive">Could not schedule annotation processing.</p>}
              </section>
              <section className="story flex flex-col gap-2 border-t border-border pt-4">
                <h4 className="text-sm font-semibold text-foreground">Story</h4>
                {detail.data.story_cluster ? (
                  <>
                    <p className="text-sm text-foreground">{detail.data.story_cluster.article_count} articles from {detail.data.story_cluster.source_count} sources</p>
                    {(detail.data.related ?? []).length > 0 && (
                      <div className="annotation-group flex flex-wrap items-center gap-2"><strong className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Also in this story</strong>{(detail.data.related ?? []).map(related => (
                        <Link key={related.article_id} className={chipClass} href={articleHref(related.article_id)}>{related.title}</Link>
                      ))}</div>
                    )}
                    <div className="flex gap-2">
                      <Link className={ghostButtonClass} href={clusterHref(detail.data.story_cluster.id, currentHref)}>Open full cluster</Link>
                      <Link className={chipClass} aria-label="Filter by story" href={refinedSearchHref('story_cluster_id', detail.data.story_cluster.id)}>Filter by story</Link>
                    </div>
                  </>
                ) : <p className="text-sm text-muted-foreground">Not part of a detected story.</p>}
              </section>
              <RelatedCoveragePanel articleId={selectedId} hrefFor={articleHref} />
              <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 border-t border-border pt-4 text-sm">
                <dt className="text-muted-foreground">Published</dt><dd className="text-foreground">{detail.data.published_at ? new Date(detail.data.published_at).toLocaleString() : 'Not supplied'}</dd>
                <dt className="text-muted-foreground">First discovered</dt><dd className="text-foreground">{new Date(detail.data.first_discovered_at).toLocaleString()}</dd>
              </dl>
              {detail.data.provenance.map(source => (
                <article key={source.feed_id} className="provenance flex flex-col gap-1 border-t border-border pt-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <Link className={cn(chipClass, 'w-fit')} aria-label={`Filter by source ${source.feed_name}`} href={refinedSearchHref('source_id', source.feed_id)}><strong>{source.feed_name}</strong></Link>
                    <Link className="text-xs text-muted-foreground hover:underline" aria-label={`Dossier for source ${source.feed_name}`} href={sourceHref(source.feed_id, currentHref)}>Dossier</Link>
                  </div>
                  <p className="text-sm text-muted-foreground">{source.description || 'No RSS description.'}</p>
                </article>
              ))}
            </>
          )}
        </GlassPanel>
      </div>
    </div>
  )
}

export default function ArticlesPage() {
  return <Suspense fallback={null}><ArticlesContent /></Suspense>
}
