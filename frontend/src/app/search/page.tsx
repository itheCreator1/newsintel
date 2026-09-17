'use client'

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import Link from 'next/link'
import { Suspense, useEffect, useState } from 'react'
import { api, ApiError } from '../../lib/api'
import type { SearchPage } from '../../lib/api-types'
import { TimelineChart } from '../../components/TimelineChart'
import { brushRange, clusterHref, INTERVALS, queryFromState, refine, searchParams, stateFromQuery, toHref, type Investigation, type ListField } from '../../lib/investigation'

const joined = (values: string[]) => values.join(', ')
const split = (value: string) => value.split(/[\s,]+/).filter(Boolean)

function formFromState(current: Investigation) {
  return {
    q: current.q, source_id: [...current.source_id], country: joined(current.source_country), after: current.after ?? '', before: current.before ?? '',
    content_available: current.content_available === null ? '' : String(current.content_available), processing_status: current.processing_status[0] ?? '', sort: current.sort,
    language: joined(current.language), entity_id: [...current.entity_id], entity_type: [...current.entity_type], keyword_id: [...current.keyword_id],
    story_country: joined(current.story_country), mentioned_country: joined(current.mentioned_country),
  }
}

const selected = (event: React.ChangeEvent<HTMLSelectElement>) => Array.from(event.target.selectedOptions, option => option.value)

function SearchContent() {
  const router = useRouter()
  const pathname = usePathname()
  const urlParams = useSearchParams()
  const client = useQueryClient()
  const currentHref = toHref(pathname, urlParams)
  const state = stateFromQuery(urlParams)
  const [form, setForm] = useState(() => formFromState(state))
  const [sourceTerm, setSourceTerm] = useState('')
  const [entityTerm, setEntityTerm] = useState('')
  const [keywordTerm, setKeywordTerm] = useState('')
  const [saveName, setSaveName] = useState('')

  useEffect(() => { setForm(formFromState(state)) /* eslint-disable-line react-hooks/exhaustive-deps */ }, [urlParams.toString()])

  const sourcePages = useInfiniteQuery({ queryKey: ['search-sources', sourceTerm], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam }) => api.searchSources(sourceTerm, pageParam), getNextPageParam: page => page.next_cursor ?? undefined })
  const entityPages = useInfiniteQuery({ queryKey: ['nlp-entities', entityTerm], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam }) => api.nlpEntities(entityTerm, pageParam), getNextPageParam: page => page.next_cursor ?? undefined })
  const keywordPages = useInfiniteQuery({ queryKey: ['nlp-keywords', keywordTerm], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam }) => api.nlpKeywords(keywordTerm, pageParam), getNextPageParam: page => page.next_cursor ?? undefined })
  const sources = sourcePages.data?.pages.flatMap(page => page.items) ?? []
  const entities = entityPages.data?.pages.flatMap(page => page.items) ?? []
  const keywords = keywordPages.data?.pages.flatMap(page => page.items) ?? []
  const criteria = searchParams(state)
  const timelineCriteria = searchParams(state, { interval: true })
  const search = useInfiniteQuery({ queryKey: ['search', criteria], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam }) => api.search(criteria, pageParam), getNextPageParam: page => page.next_cursor ?? undefined, retry: false })
  const timeline = useQuery({ queryKey: ['search-timeline', timelineCriteria], queryFn: () => api.timeline(timelineCriteria), retry: false })
  const results = search.data?.pages.flatMap(page => page.items) ?? []
  const errorCode = (reason: unknown) => reason instanceof ApiError && reason.detail && typeof reason.detail === 'object' && 'code' in reason.detail ? String(reason.detail.code) : ''
  const searchError = search.error instanceof ApiError ? search.error : null
  const expired = searchError?.status === 409 && errorCode(searchError) === 'restart_search'
  const upgradeRequired = searchError?.status === 409 && errorCode(searchError) === 'search_upgrade_required'
  const timelineTooFine = errorCode(timeline.error) === 'timeline_too_fine'
  const save = useMutation({ mutationFn: (name: string) => api.createSavedSearch(name, state), onSuccess: () => client.invalidateQueries({ queryKey: ['saved-searches'] }) })

  function navigate(next: Investigation) { router.push(toHref('/search', queryFromState(next))) }
  function submit() {
    navigate({
      ...state, q: form.q.trim(), source_id: form.source_id, source_country: split(form.country).map(code => code.toUpperCase()), after: form.after || null, before: form.before || null,
      content_available: form.content_available === '' ? null : form.content_available === 'true', processing_status: form.processing_status ? [form.processing_status] : [],
      sort: form.sort, language: split(form.language), entity_id: form.entity_id, entity_type: form.entity_type, keyword_id: form.keyword_id,
      story_country: split(form.story_country).map(code => code.toUpperCase()), mentioned_country: split(form.mentioned_country).map(code => code.toUpperCase()),
    })
  }
  function crossFilter(field: ListField, value: string) { navigate(refine(state, field, value)) }
  function selectRange(range: { start: string; end: string }) {
    // Edge buckets are calendar-aligned and can start before `after` or end after `before`; brushing must only narrow.
    const brushed = brushRange(range.start, range.end), { after, before } = state
    navigate({ ...state, after: after && after > brushed.after ? after : brushed.after, before: before && before < brushed.before ? before : brushed.before })
  }
  function setTimelineInterval(interval: string) { navigate({ ...state, interval: INTERVALS.find(item => item === interval) ?? 'auto' }) }
  function saveSearch() { if (saveName.trim()) save.mutate(saveName.trim()) }
  function restart() { search.refetch() }
  function openArticleHref(id: string) { const params = new URLSearchParams({ article: id, from: currentHref }); return toHref('/articles', params) }
  const sourceCountries = (refs: { country: string | null }[]) => [...new Set(refs.flatMap(ref => ref.country ? [ref.country] : []))]
  // A cluster's source_count is distinct feeds across the whole story; distinct_source_count is distinct
  // feeds for THIS article alone, which can already cover more than one of the cluster's feeds. The
  // affordance should only appear when other feeds beyond this article's own contribute to the cluster.
  const otherSources = (result: SearchPage['items'][number]) => (result.story_cluster?.source_count ?? 0) - result.distinct_source_count

  return (
    <>
      <header><div><p className="eyebrow">Archive discovery</p><h2>Search</h2></div></header>
      <details className="syntax-help"><summary>Query syntax</summary><p>Use quoted phrases, explicit AND, source:, country:, after:, and before:. Country means source country. Annotation clauses include entity:, keyword:, language:, story_country:, and mentioned_country:. Adjacent terms also use AND.</p></details>
      <form className="search-filters panel" role="search" onSubmit={event => { event.preventDefault(); submit() }}>
        <label className="wide">Query<input value={form.q} onChange={e => setForm(f => ({ ...f, q: e.target.value }))} placeholder='climate AND "sea level"' /></label>
        <label>Source search<input value={sourceTerm} onChange={e => setSourceTerm(e.target.value)} placeholder="Find active or retired sources" /></label>
        <label>Source<select multiple value={form.source_id} onChange={e => setForm(f => ({ ...f, source_id: selected(e) }))}>{sources.map(source => <option key={source.id} value={source.id}>{source.name}{source.retired ? ' (retired)' : ''}</option>)}</select></label>
        <label>Source country<input value={form.country} onChange={e => setForm(f => ({ ...f, country: e.target.value }))} placeholder="US, GR" /></label>
        <label>Detected language<input value={form.language} onChange={e => setForm(f => ({ ...f, language: e.target.value }))} placeholder="en" /></label>
        <label>Entity search<input value={entityTerm} onChange={e => setEntityTerm(e.target.value)} placeholder="Find an entity" /></label>
        <label>Entity<select multiple value={form.entity_id} onChange={e => setForm(f => ({ ...f, entity_id: selected(e) }))}>{entities.map(entity => <option key={entity.id} value={entity.id}>{entity.text} ({entity.kind})</option>)}</select></label>
        <label>Entity type<select multiple value={form.entity_type} onChange={e => setForm(f => ({ ...f, entity_type: selected(e) }))}>{['PERSON', 'ORG', 'GPE', 'COUNTRY', 'LOCATION', 'EVENT', 'PRODUCT', 'OTHER'].map(kind => <option key={kind}>{kind}</option>)}</select></label>
        <label>Keyword search<input value={keywordTerm} onChange={e => setKeywordTerm(e.target.value)} placeholder="Find a keyword" /></label>
        <label>Keyword<select multiple value={form.keyword_id} onChange={e => setForm(f => ({ ...f, keyword_id: selected(e) }))}>{keywords.map(keyword => <option key={keyword.id} value={keyword.id}>{keyword.text}</option>)}</select></label>
        <label>Story country<input value={form.story_country} onChange={e => setForm(f => ({ ...f, story_country: e.target.value }))} placeholder="DE" /></label>
        <label>Mentioned country<input value={form.mentioned_country} onChange={e => setForm(f => ({ ...f, mentioned_country: e.target.value }))} placeholder="FR" /></label>
        <label>After<input value={form.after} onChange={e => setForm(f => ({ ...f, after: e.target.value }))} type="date" /></label>
        <label>Before<input value={form.before} onChange={e => setForm(f => ({ ...f, before: e.target.value }))} type="date" /></label>
        <label>Content<select value={form.content_available} onChange={e => setForm(f => ({ ...f, content_available: e.target.value }))}><option value="">Any</option><option value="true">Available</option><option value="false">RSS only</option></select></label>
        <label>Processing<select value={form.processing_status} onChange={e => setForm(f => ({ ...f, processing_status: e.target.value }))}><option value="">Any</option>{['queued', 'running', 'retrying', 'succeeded', 'failed'].map(value => <option key={value}>{value}</option>)}</select></label>
        <label>Sort<select value={form.sort} onChange={e => setForm(f => ({ ...f, sort: e.target.value as Investigation['sort'] }))}><option value="relevance">Relevance</option><option value="newest">Newest</option><option value="oldest">Oldest</option><option value="most_sources">Most sources</option></select></label>
        <button type="submit">Search archive</button>
      </form>
      <section className="panel timeline" aria-labelledby="timeline-heading">
        <div className="timeline-heading">
          <h3 id="timeline-heading">{timeline.data?.total ? `${timeline.data.total} matching articles over time` : 'Timeline'}</h3>
          <div className="timeline-controls">
            {(state.after || state.before) && <button type="button" className="secondary" onClick={() => navigate({ ...state, after: null, before: null })}>Clear date range</button>}
            <label>Timeline interval
              <select value={state.interval} onChange={e => setTimelineInterval(e.target.value)}>
                {INTERVALS.map(interval => <option key={interval} value={interval}>{interval === 'auto' ? `Automatic${timeline.data && state.interval === 'auto' ? ` (${timeline.data.interval})` : ''}` : interval}</option>)}
              </select>
            </label>
          </div>
        </div>
        {timeline.isPending && <p className="muted">Charting matches…</p>}
        {!timeline.isPending && timeline.isError && (
          <>
            <p className="error">{timelineTooFine ? `${(timeline.error as ApiError).message}. Choose a larger interval.` : 'Could not load the timeline.'}</p>
            {timelineTooFine && <button type="button" className="secondary" onClick={() => setTimelineInterval('auto')}>Use automatic interval</button>}
          </>
        )}
        {!timeline.isPending && !timeline.isError && !timeline.data?.buckets.length && <p className="muted">No matching articles to chart.</p>}
        {!timeline.isPending && !timeline.isError && Boolean(timeline.data?.buckets.length) && (
          <TimelineChart buckets={timeline.data!.buckets} interval={timeline.data!.interval} onSelect={selectRange} />
        )}
      </section>
      <form className="panel save-search" onSubmit={event => { event.preventDefault(); saveSearch() }}>
        <label>Saved search name<input value={saveName} onChange={e => setSaveName(e.target.value)} maxLength={120} placeholder="Energy grid watch" /></label>
        <button type="submit" disabled={save.isPending}>Save search</button>
        {save.isError ? <p role="alert" className="error">{save.error instanceof ApiError ? save.error.message : 'Could not save this search.'}</p>
          : save.isSuccess && <p className="success">Saved “{save.variables}”.</p>}
      </form>
      <section className="panel search-results">
        {state.story_cluster_id.length > 0 && <p className="muted story-filter-banner">Filtered to one story <button type="button" className="secondary" onClick={() => navigate({ ...state, story_cluster_id: [] })}>Clear story filter</button></p>}
        {search.isPending && <p className="muted">Searching archive…</p>}
        {!search.isPending && search.isError && (
          <>
            <p role="alert" className="error">{expired ? 'This search snapshot expired. Restart the search.' : upgradeRequired ? 'Search upgrade required. Rebuild the search index to use annotation filters.' : searchError?.status === 503 ? 'Search is temporarily unavailable.' : searchError?.message || 'Could not search the archive.'}</p>
            {expired && <button className="secondary" onClick={restart}>Restart search</button>}
          </>
        )}
        {!search.isPending && !search.isError && !results.length && <p className="muted">No articles match this search.</p>}
        {results.map(result => (
          <article key={result.article_id} className="search-result">
            <button className="result-open" onClick={() => router.push(openArticleHref(result.article_id))}>
              <strong>{result.title}</strong>
              <span>{new Date(result.effective_date).toLocaleString()} · {result.distinct_source_count} sources</span>
              {result.highlights.length ? <p>{result.highlights.map((segment, index) => segment.marked ? <mark key={index}>{segment.text}</mark> : <span key={index}>{segment.text}</span>)}</p>
                : result.summary && <p>{result.summary}</p>}
            </button>
            <div className="result-filters">
              {result.source_refs.map(source => <button key={source.id} type="button" className="annotation-link" aria-label={`Filter by source ${source.name}`} onClick={() => crossFilter('source_id', source.id)}>{source.name}</button>)}
              {sourceCountries(result.source_refs).map(country => <button key={`source-${country}`} type="button" className="annotation-link" aria-label={`Filter by source country ${country}`} onClick={() => crossFilter('source_country', country)}>{country}</button>)}
              {result.story_country && <button type="button" className="annotation-link" aria-label={`Filter by story country ${result.story_country}`} onClick={() => crossFilter('story_country', result.story_country!)}>Story: {result.story_country}</button>}
              {result.story_cluster && otherSources(result) > 0 && (
                <Link className="annotation-link" href={clusterHref(result.story_cluster.id, currentHref)}>Also reported by {otherSources(result)} other source{otherSources(result) === 1 ? '' : 's'}</Link>
              )}
            </div>
          </article>
        ))}
        {search.hasNextPage && <button className="secondary" disabled={search.isFetchingNextPage} onClick={() => search.fetchNextPage()}>{search.isFetchingNextPage ? 'Loading…' : 'Load more'}</button>}
      </section>
    </>
  )
}

export default function SearchPageRoute() {
  return <Suspense fallback={null}><SearchContent /></Suspense>
}
