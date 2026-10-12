'use client'

import { keepPreviousData, useInfiniteQuery, useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import Link from 'next/link'
import { Suspense, useEffect, useId, useRef, useState } from 'react'
import { api, ApiError, exportHref } from '../../lib/api'
import type { SearchPage } from '../../lib/api-types'
import { ActiveFilterBar } from '../../components/ActiveFilterBar'
import { CheckboxPicker } from '../../components/CheckboxPicker'
import { EmptyState, ErrorNotice, LoadingState } from '../../components/Feedback'
import { TimelineChart } from '../../components/TimelineChart'
import { GlassPanel, glassPanelClassName } from '../../components/GlassPanel'
import { PageHeader } from '../../components/PageHeader'
import { EditMonitorBar } from '../../components/EditMonitorBar'
import { WatchButton } from '../../components/WatchForm'
import { FacetPanel } from '../../components/FacetPanel'
import { Popover } from '../../components/Popover'
import { ExportMenu } from '../../components/ExportMenu'
import { SourceTagPicker } from '../../components/SourceTagPicker'
import { CountryPicker, LanguagePicker } from '../../components/SearchablePicker'
import { countryLabel } from '../../lib/countries'
import { cn } from '../../lib/utils'
import { chipClass, fieldClass, ghostButtonClass, labelClass, primaryButtonClass } from '../../lib/ui-classes'
import { advancedCount, clearCriteria, errorCode, filterChips, graphHref, GRAPH_CHIP_FIELDS, isTransient, removeFilter, SEARCH_ADVANCED, SEARCH_CHIP_FIELDS, unsupportedCriteria } from '../../lib/filter-ui'
import { brushRange, clusterHref, EXPANSIONS, INTERVALS, mapHref, queryFromState, refine, searchParams, stateFromQuery, toggle, toHref, type Investigation, type ListField } from '../../lib/investigation'
import { formatDateTime } from '../../lib/datetime'

function formFromState(current: Investigation) {
  return {
    q: current.q, source_id: [...current.source_id], source_tag: [...current.source_tag], country: [...current.source_country], after: current.after ?? '', before: current.before ?? '',
    content_available: current.content_available === null ? '' : String(current.content_available), processing_status: current.processing_status[0] ?? '', sort: current.sort,
    language: [...current.language], entity_id: [...current.entity_id], entity_type: [...current.entity_type], keyword_id: [...current.keyword_id],
    story_country: [...current.story_country], mentioned_country: [...current.mentioned_country], entity_expand: [...current.entity_expand] as string[],
  }
}

const EXPANSION_OPTIONS = [{ value: 'names', label: 'Earlier and later names' }, { value: 'parts', label: 'Parts' }]

/** Clicking one fills the Query field; the clauses combine, and adjacent terms also use AND. */
const QUERY_EXAMPLES = [
  { query: '"sea level"', meaning: 'An exact phrase' },
  { query: 'climate AND flooding', meaning: 'Both words (AND is optional)' },
  { query: 'source:Reuters country:GR', meaning: 'From one source, or from sources in a country' },
  { query: 'after:2026-01-01 before:2026-02-01', meaning: 'Published in a date range (before is exclusive)' },
  { query: 'entity:"Kyriakos Mitsotakis"', meaning: 'Articles that mention an entity' },
  { query: 'keyword:inflation', meaning: 'Articles with a keyword' },
  { query: 'language:el', meaning: 'Articles detected in a language' },
  { query: 'story_country:UA mentioned_country:FR', meaning: 'About a country, or mentioning one' },
]


function SearchContent() {
  const router = useRouter()
  const pathname = usePathname()
  const urlParams = useSearchParams()
  const client = useQueryClient()
  const currentHref = toHref(pathname, urlParams)
  const state = stateFromQuery(urlParams)
  // Set while editing a monitor's criteria; every navigation keeps it so filter changes stay in the edit.
  const editing = urlParams.get('monitor') ?? ''
  const [form, setForm] = useState(() => formFromState(state))
  const [sourceTerm, setSourceTerm] = useState('')
  const [entityTerm, setEntityTerm] = useState('')
  const [keywordTerm, setKeywordTerm] = useState('')
  const [saveName, setSaveName] = useState('')
  const queryInput = useRef<HTMLInputElement>(null)
  const searchFormId = useId()
  const advanced = advancedCount(state, SEARCH_ADVANCED, { content: true })
  const [advancedOpen, setAdvancedOpen] = useState(advanced > 0)

  // Every URL change (submit, chip, cross-filter, Back) resyncs the form, and reopens Advanced when it holds criteria.
  useEffect(() => {
    setForm(formFromState(state))
    if (advanced > 0) setAdvancedOpen(true)
  }, [urlParams.toString()]) // eslint-disable-line react-hooks/exhaustive-deps

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
  // Facets ignore sort, so re-sorting results never refetches them.
  const facetCriteria = Object.fromEntries(Object.entries(criteria).filter(([key]) => key !== 'sort'))
  // Keeping the previous counts during a recount keeps the pressed facet, and its focus, on screen.
  const facets = useQuery({ queryKey: ['search-facets', facetCriteria], queryFn: () => api.facets(facetCriteria), retry: false, placeholderData: keepPreviousData })
  const facetsUpgrade = errorCode(facets.error) === 'search_upgrade_required'
  const facetsDown = facets.error instanceof ApiError && facets.error.status === 503
  // A value selected from a facet need not be on the pickers' loaded pages, so chips also take facet labels.
  const facetLabels = (group: 'sources' | 'entities' | 'keywords' | 'story_clusters') =>
    (facets.data?.[group].buckets ?? []).flatMap(bucket => bucket.label ? [[bucket.value, bucket.label] as const] : [])
  const ignoredByGraph = unsupportedCriteria(state, GRAPH_CHIP_FIELDS)
  const results = search.data?.pages.flatMap(page => page.items) ?? []
  const searchError = search.error instanceof ApiError ? search.error : null
  const expired = searchError?.status === 409 && errorCode(searchError) === 'restart_search'
  const upgradeRequired = searchError?.status === 409 && errorCode(searchError) === 'search_upgrade_required'
  const timelineTooFine = errorCode(timeline.error) === 'timeline_too_fine'
  const save = useMutation({ mutationFn: (name: string) => api.createSavedSearch(name, state), onSuccess: () => client.invalidateQueries({ queryKey: ['saved-searches'] }) })

  function withMonitor(query: URLSearchParams) { if (editing) query.set('monitor', editing); return query }
  function navigate(next: Investigation) { router.push(toHref('/search', withMonitor(queryFromState(next)))) }
  function draftState(draft = form): Investigation {
    return {
      ...state, q: draft.q.trim(), source_id: draft.source_id, source_tag: draft.source_tag, source_country: draft.country, after: draft.after || null, before: draft.before || null,
      content_available: draft.content_available === '' ? null : draft.content_available === 'true', processing_status: draft.processing_status ? [draft.processing_status] : [],
      sort: draft.sort, language: draft.language, entity_id: draft.entity_id, entity_type: draft.entity_type, keyword_id: draft.keyword_id,
      story_country: draft.story_country, mentioned_country: draft.mentioned_country,
      entity_expand: EXPANSIONS.filter(option => draft.entity_expand.includes(option)),
    }
  }
  function submit() { navigate(draftState()) }
  // Compared through the same form round trip, so a URL the form cannot represent exactly never reads as an edit.
  const draftDiffers = queryFromState(draftState()).toString() !== queryFromState(draftState(formFromState(state))).toString()
  const resultEntities = results.flatMap(result => result.entities.map(entity => [entity.id, entity.text] as const))
  const pickerLabels = {
    source_id: new Map([...facetLabels('sources'), ...sources.map(source => [source.id, source.name] as const)]),
    entity_id: new Map([...resultEntities, ...facetLabels('entities'), ...entities.map(entity => [entity.id, entity.text] as const)]),
    keyword_id: new Map([...facetLabels('keywords'), ...keywords.map(keyword => [keyword.id, keyword.text] as const)]),
  }
  // An event filter comes from an event's page, so its chip is named by that event's headline.
  const events = useQueries({ queries: state.event_id.map(id => ({ queryKey: ['event', id], queryFn: () => api.event(id), retry: false })) })
  const eventLabels = new Map(events.flatMap(event => event.data?.headline ? [[event.data.id, event.data.headline] as const] : []))
  const chips = filterChips(state, SEARCH_CHIP_FIELDS, { ...pickerLabels, story_cluster_id: new Map(facetLabels('story_clusters')), event_id: eventLabels }, { content: true })
  function clearAll() {
    setSourceTerm(''); setEntityTerm(''); setKeywordTerm('')
    navigate(clearCriteria(state))
  }
  function crossFilter(field: ListField, value: string) { navigate(refine(state, field, value)) }
  function selectRange(range: { start: string; end: string }) {
    // Edge buckets are calendar-aligned and can start before `after` or end after `before`; brushing must only narrow.
    const brushed = brushRange(range.start, range.end), { after, before } = state
    navigate({ ...state, after: after && after > brushed.after ? after : brushed.after, before: before && before < brushed.before ? before : brushed.before })
  }
  function setTimelineInterval(interval: string) { navigate({ ...state, interval: INTERVALS.find(item => item === interval) ?? 'auto' }) }
  function applyExample(query: string) {
    setForm(f => ({ ...f, q: query }))
    queryInput.current?.focus()
  }
  function saveSearch() { if (saveName.trim()) save.mutate(saveName.trim()) }
  function restart() { search.refetch() }
  function openArticleHref(id: string) { const params = new URLSearchParams({ article: id, from: currentHref }); return toHref('/articles', params) }
  const sourceCountries = (refs: { country: string | null }[]) => [...new Set(refs.flatMap(ref => ref.country ? [ref.country] : []))]
  // A cluster's source_count is distinct feeds across the whole story; distinct_source_count is distinct
  // feeds for THIS article alone, which can already cover more than one of the cluster's feeds. The
  // affordance should only appear when other feeds beyond this article's own contribute to the cluster.
  const otherSources = (result: SearchPage['items'][number]) => (result.story_cluster?.source_count ?? 0) - result.distinct_source_count

  return (
    <div className="flex flex-col gap-6 font-sans">
      <PageHeader eyebrow="Archive discovery" title="Search" description="Find articles and narrow your investigation with filters." />

      <details className="rounded-2xl border border-border bg-card/40 px-5 py-4 text-sm text-muted-foreground backdrop-blur-xl">
        <summary className="cursor-pointer font-medium text-foreground">Query syntax</summary>
        <p className="mt-2 leading-relaxed">Country means source country. Click an example to put it in the Query field.</p>
        <table aria-label="Query examples" className="mt-3 w-full text-left text-sm">
          <tbody>
            {QUERY_EXAMPLES.map(example => (
              <tr key={example.query} className="border-t border-border/60">
                <td className="py-1.5 pr-4"><button type="button" className={cn(chipClass, 'font-mono')} onClick={() => applyExample(example.query)}>{example.query}</button></td>
                <td className="py-1.5">{example.meaning}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>

      <div className={cn(glassPanelClassName, 'flex flex-col gap-4')}>
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end">
          <form id={searchFormId} className="grid flex-1 grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-[2fr_1fr_1fr_1fr]" role="search" onSubmit={event => { event.preventDefault(); submit() }}>
            <label className={labelClass}>Query<input ref={queryInput} className={cn(fieldClass, 'mt-1')} value={form.q} onChange={e => setForm(f => ({ ...f, q: e.target.value }))} placeholder='climate AND "sea level"' /></label>
            <label className={labelClass}>After<input className={cn(fieldClass, 'mt-1')} value={form.after} onChange={e => setForm(f => ({ ...f, after: e.target.value }))} type="date" /></label>
            <div className="flex flex-col gap-1">
              <label className={labelClass}>Before<input aria-describedby="search-before-help" className={cn(fieldClass, 'mt-1')} value={form.before} onChange={e => setForm(f => ({ ...f, before: e.target.value }))} type="date" /></label>
              <p id="search-before-help" className="text-[11px] text-muted-foreground">Exclusive: up to the start of this day.</p>
            </div>
            <label className={labelClass}>Sort<select className={cn(fieldClass, 'mt-1')} value={form.sort} onChange={e => setForm(f => ({ ...f, sort: e.target.value as Investigation['sort'] }))}><option value="relevance">Relevance</option><option value="newest">Newest</option><option value="oldest">Oldest</option><option value="most_sources">Most sources</option></select></label>
          </form>
          {/* Outside the search form: Save and Watch hold forms of their own. */}
          <div className="flex flex-wrap items-stretch gap-2 lg:mb-5">
            <button type="submit" form={searchFormId} className={primaryButtonClass}>Search archive</button>
            <Popover label="Save" title="Save search">
              <form className="flex flex-col gap-3" onSubmit={event => { event.preventDefault(); saveSearch() }}>
                <label className={labelClass}>Saved search name<input className={cn(fieldClass, 'mt-1')} value={saveName} onChange={e => setSaveName(e.target.value)} maxLength={120} placeholder="Energy grid watch" /></label>
                <button type="submit" disabled={save.isPending} className={primaryButtonClass}>{save.isPending ? 'Saving…' : 'Save search'}</button>
                {save.isError ? <p role="alert" className="error text-sm text-destructive">{save.error instanceof ApiError ? save.error.message : 'Could not save this search.'}</p>
                  : save.isSuccess && <p role="status" className="text-sm text-primary">Saved “{save.variables}”.</p>}
              </form>
            </Popover>
            {!editing && <WatchButton state={state} />}
          </div>
        </div>
        {/* Collapsing hides the fields but keeps them mounted, so draft edits survive. Search archive applies them. */}
        <details open={advancedOpen} onToggle={event => setAdvancedOpen(event.currentTarget.open)}>
          <summary className="cursor-pointer text-sm font-medium text-foreground">Advanced filters{advanced > 0 ? ` (${advanced})` : ''}</summary>
          <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <label className={labelClass}>Source search<input className={cn(fieldClass, 'mt-1')} value={sourceTerm} onChange={e => setSourceTerm(e.target.value)} placeholder="Find active or retired sources" /></label>
            <CheckboxPicker legend="Source" options={sources.map(source => ({ value: source.id, label: `${source.name}${source.retired ? ' (retired)' : ''}` }))} selected={form.source_id} labels={pickerLabels.source_id} onChange={values => setForm(f => ({ ...f, source_id: values }))} emptyText="No sources match." />
            <SourceTagPicker selected={form.source_tag} onChange={values => setForm(f => ({ ...f, source_tag: values }))} />
            <CountryPicker legend="Source country" selected={form.country} onChange={values => setForm(f => ({ ...f, country: values }))} />
            <LanguagePicker legend="Detected language" selected={form.language} onChange={values => setForm(f => ({ ...f, language: values }))} />
            <label className={labelClass}>Entity search<input className={cn(fieldClass, 'mt-1')} value={entityTerm} onChange={e => setEntityTerm(e.target.value)} placeholder="Find an entity" /></label>
            <CheckboxPicker legend="Entity" options={entities.map(entity => ({ value: entity.id, label: `${entity.text} (${entity.kind})` }))} selected={form.entity_id} labels={pickerLabels.entity_id} onChange={values => setForm(f => ({ ...f, entity_id: values }))} emptyText="No entities match." />
            <CheckboxPicker legend="Follow see-also links" options={EXPANSION_OPTIONS} selected={form.entity_expand} onChange={values => setForm(f => ({ ...f, entity_expand: values }))} />
            <CheckboxPicker legend="Entity type" options={['PERSON', 'ORG', 'GPE', 'COUNTRY', 'LOCATION', 'EVENT', 'PRODUCT', 'TOPIC', 'OTHER'].map(kind => ({ value: kind, label: kind }))} selected={form.entity_type} onChange={values => setForm(f => ({ ...f, entity_type: values }))} />
            <label className={labelClass}>Keyword search<input className={cn(fieldClass, 'mt-1')} value={keywordTerm} onChange={e => setKeywordTerm(e.target.value)} placeholder="Find a keyword" /></label>
            <CheckboxPicker legend="Keyword" options={keywords.map(keyword => ({ value: keyword.id, label: keyword.text }))} selected={form.keyword_id} labels={pickerLabels.keyword_id} onChange={values => setForm(f => ({ ...f, keyword_id: values }))} emptyText="No keywords match." />
            <CountryPicker legend="Story country" selected={form.story_country} onChange={values => setForm(f => ({ ...f, story_country: values }))} />
            <CountryPicker legend="Mentioned country" selected={form.mentioned_country} onChange={values => setForm(f => ({ ...f, mentioned_country: values }))} />
            <label className={labelClass}>Content<select className={cn(fieldClass, 'mt-1')} value={form.content_available} onChange={e => setForm(f => ({ ...f, content_available: e.target.value }))}><option value="">Any</option><option value="true">Available</option><option value="false">RSS only</option></select></label>
            <label className={labelClass}>Processing<select className={cn(fieldClass, 'mt-1')} value={form.processing_status} onChange={e => setForm(f => ({ ...f, processing_status: e.target.value }))}><option value="">Any</option>{['queued', 'running', 'retrying', 'succeeded', 'failed'].map(value => <option key={value}>{value}</option>)}</select></label>
          </div>
        </details>
      </div>

      <ActiveFilterBar items={chips} onRemove={key => navigate(removeFilter(state, key))} onClear={clearAll} draftDiffers={draftDiffers} />

      {editing && <EditMonitorBar key={editing} id={editing} state={state} stopHref={toHref('/search', queryFromState(state))} />}

      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <aside aria-label="Refine results" className="flex flex-col gap-4 lg:sticky lg:top-6">
          <GlassPanel aria-labelledby="facets-heading" className="p-4">
            <h3 id="facets-heading" className="mb-4 text-sm font-semibold text-foreground">Refine by</h3>
            {facets.isPending && <LoadingState label="Counting facets…" />}
            {facets.isError && (
              <ErrorNotice
                message={facetsUpgrade ? 'Search upgrade required. Rebuild the search index to see facets.' : facetsDown ? 'Facets are temporarily unavailable.' : 'Could not load facets.'}
                onRetry={isTransient(facets.error) ? () => void facets.refetch() : undefined} retrying={facets.isFetching}
              />
            )}
            {facets.data && <FacetPanel facets={facets.data} state={state} onToggle={(field, value) => navigate(toggle(state, field, value))} />}
          </GlassPanel>
        </aside>

        <div className="flex min-w-0 flex-col gap-6">
          <p className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
            <Link className={cn(ghostButtonClass, 'w-auto')} href={graphHref(state)}>Open in Graph</Link>
            {ignoredByGraph.length > 0 && <span>Graph does not apply: {ignoredByGraph.join(', ')}.</span>}
            <Link className={cn(ghostButtonClass, 'w-auto')} href={mapHref({ investigation: state })}>Open in Map</Link>
            <ExportMenu className="ml-auto" hrefFor={format => exportHref.search(criteria, format)} />
          </p>
          <GlassPanel aria-labelledby="timeline-heading" className="p-4">
            <div className="mb-4 flex flex-wrap items-end justify-between gap-4">
              <h3 id="timeline-heading" className="text-sm font-semibold text-foreground">{timeline.data?.total ? `${timeline.data.total} matching articles over time` : 'Timeline'}</h3>
              <div className="flex flex-wrap items-end gap-3">
                {(state.after || state.before) && <button type="button" className={ghostButtonClass} onClick={() => navigate({ ...state, after: null, before: null })}>Clear date range</button>}
                <label className={labelClass}>Timeline interval
                  <select className={cn(fieldClass, 'mt-1')} value={state.interval} onChange={e => setTimelineInterval(e.target.value)}>
                    {INTERVALS.map(interval => <option key={interval} value={interval}>{interval === 'auto' ? `Automatic${timeline.data && state.interval === 'auto' ? ` (${timeline.data.interval})` : ''}` : interval}</option>)}
                  </select>
                </label>
              </div>
            </div>
            {timeline.isPending && <LoadingState label="Charting matches…" variant="chart" />}
            {timeline.isError && (timelineTooFine ? (
              <>
                <p className="error text-sm text-destructive">{`${(timeline.error as ApiError).message}. Choose a larger interval.`}</p>
                <button type="button" className={cn(ghostButtonClass, 'mt-2')} onClick={() => setTimelineInterval('auto')}>Use automatic interval</button>
              </>
            ) : <ErrorNotice message="Could not load the timeline." onRetry={isTransient(timeline.error) ? () => void timeline.refetch() : undefined} retrying={timeline.isFetching} />)}
            {timeline.isSuccess && !timeline.data.buckets.length && <p className="text-sm text-muted-foreground">No matching articles to chart.</p>}
            {Boolean(timeline.data?.buckets.length) && (
              <TimelineChart buckets={timeline.data!.buckets} interval={timeline.data!.interval} onSelect={selectRange} />
            )}
          </GlassPanel>
          <GlassPanel aria-label="Results" className="overflow-hidden p-0">
            {state.story_cluster_id.length > 0 && (
              <p className="flex items-center gap-3 px-6 py-4 text-sm text-muted-foreground">Filtered to one story <button type="button" className={ghostButtonClass} onClick={() => navigate({ ...state, story_cluster_id: [] })}>Clear story filter</button></p>
            )}
            {search.isPending && <div className="px-6 py-4"><LoadingState label="Searching archive…" /></div>}
            {search.isError && (
              <div className="px-6 py-4">
                {expired ? (
                  <>
                    <p role="alert" className="error text-sm text-destructive">This search snapshot expired. Restart the search.</p>
                    <button className={cn(ghostButtonClass, 'mt-2')} onClick={restart}>Restart search</button>
                  </>
                ) : (
                  <ErrorNotice
                    message={upgradeRequired ? 'Search upgrade required. Rebuild the search index to use annotation filters.' : searchError?.status === 503 ? 'Search is temporarily unavailable.' : searchError?.message || 'Could not search the archive.'}
                    onRetry={!upgradeRequired && isTransient(search.error) ? restart : undefined} retrying={search.isFetching}
                  />
                )}
              </div>
            )}
            {search.isSuccess && !results.length && (
              <div className="px-6 py-4">
                {chips.length
                  ? <EmptyState title="No articles match this search." description="Try removing a filter or widening the dates." action={<button type="button" className={cn(ghostButtonClass, 'w-auto')} onClick={clearAll}>Clear all filters</button>} />
                  : <EmptyState title="No articles match this search." description="Matches appear once sources have been collected and indexed." />}
              </div>
            )}
            {results.map(result => (
              <article key={result.article_id} className="search-result border-border px-6 py-4 last:border-b-0 hover:bg-accent/40">
                <button className="result-open transition-colors" onClick={() => router.push(openArticleHref(result.article_id))}>
                  <span className="flex items-start gap-2">
                    <strong className="text-[15px] font-semibold text-foreground">{result.title}</strong>
                    {result.starred && <span className="ml-auto text-primary" role="img" aria-label="Starred">★</span>}
                  </span>
                  <span className="text-xs text-muted-foreground">{formatDateTime(result.effective_date)} · {result.distinct_source_count} sources{result.read && <> · <span>Read</span></>}</span>
                  {result.highlights.length ? <p className="text-sm text-muted-foreground [&_mark]:rounded [&_mark]:bg-primary/25 [&_mark]:text-foreground">{result.highlights.map((segment, index) => segment.marked ? <mark key={index}>{segment.text}</mark> : <span key={index}>{segment.text}</span>)}</p>
                    : result.summary && <p className="text-sm text-muted-foreground">{result.summary}</p>}
                  {result.note && <p className="border-l-2 border-primary pl-2 text-sm text-foreground"><span className="sr-only">Your note: </span>{result.note}</p>}
                </button>
                <div className="result-filters mt-2 flex flex-wrap gap-2">
                  {result.source_refs.map(source => <button key={source.id} type="button" className={chipClass} aria-label={`Filter by source ${source.name}`} onClick={() => crossFilter('source_id', source.id)}>{source.name}</button>)}
                  {sourceCountries(result.source_refs).map(country => <button key={`source-${country}`} type="button" className={chipClass} aria-label={`Filter by source country ${country}`} onClick={() => crossFilter('source_country', country)}>{countryLabel(country)}</button>)}
                  {result.story_country && <button type="button" className={chipClass} aria-label={`Filter by story country ${result.story_country}`} onClick={() => crossFilter('story_country', result.story_country!)}>Story: {countryLabel(result.story_country)}</button>}
                  {result.entities.map(entity => <button key={`entity-${entity.id}`} type="button" className={chipClass} aria-label={`Filter by entity ${entity.text} (${entity.type})`} onClick={() => crossFilter('entity_id', entity.id)}>{entity.text}</button>)}
                  {result.story_cluster && otherSources(result) > 0 && (
                    <Link className={chipClass} href={clusterHref(result.story_cluster.id, currentHref)}>Also reported by {otherSources(result)} other source{otherSources(result) === 1 ? '' : 's'}</Link>
                  )}
                </div>
              </article>
            ))}
            {search.hasNextPage && <div className="px-6 py-4"><button className={ghostButtonClass} disabled={search.isFetchingNextPage} onClick={() => search.fetchNextPage()}>{search.isFetchingNextPage ? 'Loading…' : 'Load more'}</button></div>}
          </GlassPanel>
        </div>
      </div>
    </div>
  )
}

export default function SearchPageRoute() {
  return <Suspense fallback={null}><SearchContent /></Suspense>
}
