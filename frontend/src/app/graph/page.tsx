'use client'

import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useEffect, useState } from 'react'
import { EdgeEvidencePanel } from '../../components/EdgeEvidencePanel'
import { ActiveFilterBar } from '../../components/ActiveFilterBar'
import { EntityGraph } from '../../components/EntityGraph'
import { EmptyState, ErrorNotice, LoadingState } from '../../components/Feedback'
import { GlassPanel, glassPanelClassName } from '../../components/GlassPanel'
import { PageHeader } from '../../components/PageHeader'
import { chipClass, fieldClass, ghostButtonClass, labelClass, primaryButtonClass } from '../../lib/ui-classes'
import { advancedCount, filterChips, GRAPH_ADVANCED, GRAPH_CHIP_FIELDS, isTransient, removeFilter } from '../../lib/filter-ui'
import { cn } from '../../lib/utils'
import { api, ApiError } from '../../lib/api'
import { emptyInvestigation, entityHref, queryFromState, refine, stateFromQuery, toHref, type Investigation } from '../../lib/investigation'

const MAX_NODES = 50
const split = (value: string) => value.split(/[\s,]+/).filter(Boolean)
const edgeKey = (a: string, b: string) => [a, b].sort().join(':')

function formFromState(current: Investigation, nodeCount: number) {
  return {
    q: current.q, source_id: [...current.source_id], country: current.source_country.join(', '), story_country: current.story_country.join(', '),
    entity_type: [...current.entity_type], after: current.after ?? '', before: current.before ?? '', nodes: String(nodeCount),
  }
}

function GraphContent() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const state = stateFromQuery(searchParams)
  const focus = searchParams.get('focus') ?? ''
  const edge = (() => {
    const [source, target, ...rest] = (searchParams.get('edge') ?? '').split(':')
    return source && target && !rest.length && source !== target ? [source, target] as const : null
  })()
  const nodeCount = (() => {
    const raw = Number(searchParams.get('nodes'))
    // A hand-edited URL or bookmark can carry any value; the backend rejects anything over MAX_NODES with a 422.
    return Number.isFinite(raw) && raw > 0 ? Math.min(Math.floor(raw), MAX_NODES) : 30
  })()
  const [form, setForm] = useState(() => formFromState(state, nodeCount))
  const [sourceTerm, setSourceTerm] = useState('')
  const advanced = advancedCount(state, GRAPH_ADVANCED)
  const [advancedOpen, setAdvancedOpen] = useState(advanced > 0)

  // Every URL change resyncs the form, and reopens Advanced when it holds criteria.
  useEffect(() => {
    setForm(formFromState(state, nodeCount))
    if (advanced > 0) setAdvancedOpen(true)
  }, [searchParams.toString()]) // eslint-disable-line react-hooks/exhaustive-deps

  const sourcePages = useInfiniteQuery({ queryKey: ['graph-sources', sourceTerm], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam }) => api.searchSources(sourceTerm, pageParam), getNextPageParam: page => page.next_cursor ?? undefined })
  const sources = sourcePages.data?.pages.flatMap(page => page.items) ?? []

  // Only the fields the graph filter form exposes are sent: the graph endpoint accepts more (entity_id,
  // keyword_id, story_cluster_id, ...) via the shared search criteria, but leaking whatever happens to be
  // in the URL from another view would silently change results the user never asked to filter by.
  const evidenceFilters: Record<string, string | string[] | undefined> = {}
  if (state.q) evidenceFilters.q = state.q
  if (state.source_id.length) evidenceFilters.source_id = state.source_id
  if (state.source_country.length) evidenceFilters.source_country = state.source_country
  if (state.story_country.length) evidenceFilters.story_country = state.story_country
  if (state.entity_type.length) evidenceFilters.entity_type = state.entity_type
  if (state.after) evidenceFilters.after = state.after
  if (state.before) evidenceFilters.before = state.before
  if (focus) evidenceFilters.focus_entity_id = focus
  // Edge evidence takes every graph filter except the node count, so its totals equal the drawn edge weight.
  const graphFilters = { ...evidenceFilters, nodes: String(nodeCount) }

  const graph = useQuery({ queryKey: ['entity-graph', graphFilters], queryFn: () => api.entityGraph(graphFilters), retry: false })
  const nodes = graph.data?.nodes ?? []
  const edges = graph.data?.edges ?? []
  const focusNode = nodes.find(node => node.id === focus) ?? null
  const nodeById = new Map(nodes.map(node => [node.id, node]))
  const selectedEdge = edge ? edgeKey(...edge) : ''
  const connected = focusNode
    ? nodes.filter(node => new Set(edges.filter(edge => edge.source === focusNode.id || edge.target === focusNode.id).map(edge => edge.source === focusNode.id ? edge.target : edge.source)).has(node.id))
    : []
  const articleCriteria = focusNode ? {
    q: state.q || undefined, source_country: state.source_country.length ? state.source_country : undefined,
    story_country: state.story_country.length ? state.story_country : undefined, after: state.after ?? undefined,
    before: state.before ?? undefined, entity_id: [focusNode.id],
  } : null
  const articles = useQuery({ queryKey: ['entity-graph-articles', articleCriteria], queryFn: () => api.search(articleCriteria!), enabled: Boolean(articleCriteria), retry: false })
  const errorCode = (reason: unknown) => reason instanceof ApiError && reason.detail && typeof reason.detail === 'object' && 'code' in reason.detail ? String(reason.detail.code) : ''
  const graphError = graph.error instanceof ApiError ? graph.error : null
  const upgradeRequired = graphError?.status === 409 && errorCode(graphError) === 'search_upgrade_required'

  function navigate(next: Investigation, extra: { focus?: string; nodes?: number; edge?: string } = {}) {
    const query = queryFromState(next)
    const nextFocus = extra.focus !== undefined ? extra.focus : focus
    const nextNodes = Math.min(extra.nodes !== undefined ? extra.nodes : nodeCount, MAX_NODES)
    if (nextFocus) query.set('focus', nextFocus)
    if (extra.edge) query.set('edge', extra.edge)
    if (nextNodes !== 30) query.set('nodes', String(nextNodes))
    router.push(toHref('/graph', query))
  }
  function draftState(draft = form): Investigation {
    return {
      ...state, q: draft.q.trim(), source_id: draft.source_id, source_country: split(draft.country).map(code => code.toUpperCase()),
      story_country: split(draft.story_country).map(code => code.toUpperCase()), entity_type: draft.entity_type,
      after: draft.after || null, before: draft.before || null,
    }
  }
  function submit() { navigate(draftState(), { nodes: Number(form.nodes) || 30 }) }
  const draftKey = (draft: typeof form) => `${queryFromState(draftState(draft))}|${Number(draft.nodes) || 30}`
  const draftDiffers = draftKey(form) !== draftKey(formFromState(state, nodeCount))
  const chips = [
    ...filterChips(state, GRAPH_CHIP_FIELDS, { source_id: new Map(sources.map(source => [source.id, source.name])) }),
    ...(focus ? [{ key: 'focus', label: `Focused entity: ${focusNode?.text ?? focus}` }] : []),
  ]
  // Any criterion change drops the selected edge (navigate never carries it); only the focus chip drops focus.
  function removeChip(key: string) { navigate(key === 'focus' ? state : removeFilter(state, key), key === 'focus' ? { focus: '' } : {}) }
  function clearAll() { setSourceTerm(''); navigate(emptyInvestigation(), { focus: '' }) }
  function selectEntity(entityId: string) { navigate(state, { focus: entityId }) }
  function selectEdge(source: string, target: string) { navigate(state, { edge: edgeKey(source, target) }) }
  function searchWithEntityHref(entityId: string) { return toHref('/search', queryFromState(refine(state, 'entity_id', entityId))) }
  const selected = (event: React.ChangeEvent<HTMLSelectElement>) => Array.from(event.target.selectedOptions, option => option.value)

  return (
    <div className="flex flex-col gap-6 font-sans">
      <PageHeader eyebrow="Relationships" title="Graph" description="Explore entities mentioned together and inspect the supporting articles." />
      <form className={cn(glassPanelClassName, 'grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4')} role="search" onSubmit={event => { event.preventDefault(); submit() }}>
        <label className={cn(labelClass, 'sm:col-span-2')}>Query<input className={cn(fieldClass, 'mt-1')} value={form.q} onChange={e => setForm(f => ({ ...f, q: e.target.value }))} placeholder='climate AND "sea level"' /></label>
        <label className={labelClass}>After<input className={cn(fieldClass, 'mt-1')} value={form.after} onChange={e => setForm(f => ({ ...f, after: e.target.value }))} type="date" /></label>
        <div className="flex flex-col gap-1">
          <label className={labelClass}>Before<input aria-describedby="graph-before-help" className={cn(fieldClass, 'mt-1')} value={form.before} onChange={e => setForm(f => ({ ...f, before: e.target.value }))} type="date" /></label>
          <p id="graph-before-help" className="text-[11px] text-muted-foreground">Exclusive: up to the start of this day.</p>
        </div>
        <label className={labelClass}>Nodes<input className={cn(fieldClass, 'mt-1')} value={form.nodes} onChange={e => setForm(f => ({ ...f, nodes: e.target.value }))} type="number" min={1} max={50} /></label>
        <button type="submit" className={cn(primaryButtonClass, 'self-end sm:col-span-2 lg:col-span-1 lg:col-start-4')}>Update graph</button>
        {/* Collapsing hides the fields but keeps them mounted, so draft edits survive. */}
        <details className="sm:col-span-2 lg:col-span-4" open={advancedOpen} onToggle={event => setAdvancedOpen(event.currentTarget.open)}>
          <summary className="cursor-pointer text-sm font-medium text-foreground">Advanced filters{advanced > 0 ? ` (${advanced})` : ''}</summary>
          <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <label className={labelClass}>Source search<input className={cn(fieldClass, 'mt-1')} value={sourceTerm} onChange={e => setSourceTerm(e.target.value)} placeholder="Find active or retired sources" /></label>
            <label className={labelClass}>Source<select className={cn(fieldClass, 'mt-1')} multiple value={form.source_id} onChange={e => setForm(f => ({ ...f, source_id: selected(e) }))}>{sources.map(source => <option key={source.id} value={source.id}>{source.name}{source.retired ? ' (retired)' : ''}</option>)}</select></label>
            <label className={labelClass}>Source country<input className={cn(fieldClass, 'mt-1')} value={form.country} onChange={e => setForm(f => ({ ...f, country: e.target.value }))} placeholder="US, GR" /></label>
            <label className={labelClass}>Story country<input className={cn(fieldClass, 'mt-1')} value={form.story_country} onChange={e => setForm(f => ({ ...f, story_country: e.target.value }))} placeholder="DE" /></label>
            <label className={labelClass}>Entity type<select className={cn(fieldClass, 'mt-1')} multiple value={form.entity_type} onChange={e => setForm(f => ({ ...f, entity_type: selected(e) }))}>{['PERSON', 'ORG', 'GPE', 'COUNTRY', 'LOCATION', 'EVENT', 'PRODUCT', 'OTHER'].map(kind => <option key={kind}>{kind}</option>)}</select></label>
          </div>
        </details>
      </form>

      <ActiveFilterBar items={chips} onRemove={removeChip} onClear={clearAll} draftDiffers={draftDiffers} />

      <div className={focusNode || edge ? 'grid gap-6 lg:grid-cols-[minmax(0,1.6fr)_minmax(280px,1fr)]' : undefined}>
        <GlassPanel>
          {graph.isPending && <LoadingState label="Loading the entity graph…" variant="chart" />}
          {graph.isError && (
            <ErrorNotice
              message={upgradeRequired ? 'Search upgrade required. Rebuild the search index to use the entity graph.' : graphError?.status === 503 ? 'The entity graph is temporarily unavailable.' : 'Could not load the entity graph.'}
              onRetry={!upgradeRequired && isTransient(graph.error) ? () => void graph.refetch() : undefined} retrying={graph.isFetching}
            />
          )}
          {graph.isSuccess && !nodes.length && (chips.length
            ? <EmptyState title="No co-occurring entities for these filters." description="Try removing a filter or widening the dates." action={<button type="button" className={cn(ghostButtonClass, 'w-auto')} onClick={clearAll}>Clear all filters</button>} />
            : <EmptyState title="No co-occurring entities yet." description="Entities appear once articles have been processed." />)}
          {nodes.length > 0 && (
            <>
              <EntityGraph nodes={nodes} edges={edges} focus={focus} onSelect={selectEntity} onSelectEdge={selectEdge} />
              {graph.data?.truncated && <p className="mt-2 text-sm text-muted-foreground">Showing a bounded subset of the graph. Narrow the filters to see more.</p>}
              <ul className="mt-4 flex flex-wrap gap-2" aria-label="Entities in this graph">
                {nodes.map(node => (
                  <li key={node.id}>
                    <button
                      type="button"
                      className={cn(chipClass, node.id === focus && 'border-primary/60 bg-primary/12 text-foreground')}
                      aria-pressed={node.id === focus}
                      onClick={() => selectEntity(node.id)}
                    >
                      {node.text} ({node.type}) · {node.article_count}
                    </button>
                  </li>
                ))}
              </ul>
              {edges.length > 0 && (
                <ul className="mt-3 flex flex-wrap gap-2" aria-label="Connections in this graph">
                  {edges.map(link => {
                    const from = nodeById.get(link.source)
                    const to = nodeById.get(link.target)
                    if (!from || !to) return null
                    const key = edgeKey(link.source, link.target)
                    return (
                      <li key={key}>
                        <button type="button" className={cn(chipClass, key === selectedEdge && 'border-primary/60 bg-primary/12 text-foreground')} aria-pressed={key === selectedEdge} onClick={() => selectEdge(link.source, link.target)}>
                          {from.text} — {to.text} · {link.weight}
                        </button>
                      </li>
                    )
                  })}
                </ul>
              )}
            </>
          )}
        </GlassPanel>
        {(focusNode || edge) && <div className="flex flex-col gap-6">
        {edge && <EdgeEvidencePanel source={edge[0]} target={edge[1]} filters={evidenceFilters} returnHref={toHref('/graph', new URLSearchParams(searchParams.toString()))} />}
        {focusNode && (
          <aside aria-label="Entity details" className={cn(glassPanelClassName, 'flex flex-col gap-3')}>
            <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-primary/80">{focusNode.type}</p>
            <h3 className="text-lg font-semibold text-foreground">{focusNode.text}</h3>
            <p className="text-sm text-muted-foreground">{focusNode.article_count} articles</p>
            <Link className={cn(chipClass, 'w-fit')} href={searchWithEntityHref(focusNode.id)}>Search articles with {focusNode.text}</Link>
            <Link className={cn(chipClass, 'w-fit')} href={entityHref(focusNode.id)}>Open dossier for {focusNode.text}</Link>
            <div>
              <strong className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Connected entities</strong>
              <div className="mt-2 flex flex-wrap gap-2">
                {connected.map(node => <button key={node.id} type="button" className={chipClass} onClick={() => selectEntity(node.id)}>{node.text}</button>)}
                {!connected.length && <span className="text-sm text-muted-foreground">None</span>}
              </div>
            </div>
            <div>
              <strong className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Top articles</strong>
              <div className="mt-2 flex flex-col gap-2">
                {articles.isPending && <p className="text-sm text-muted-foreground">Loading articles…</p>}
                {!articles.isPending && articles.isError && <p className="error text-sm text-destructive">Could not load articles.</p>}
                {!articles.isPending && !articles.isError && !articles.data?.items.length && <p className="text-sm text-muted-foreground">No matching articles.</p>}
                {(articles.data?.items ?? []).map(result => (
                  <Link key={result.article_id} className="text-sm text-primary underline-offset-4 hover:underline" href={toHref('/articles', new URLSearchParams({ article: result.article_id, from: toHref('/graph', new URLSearchParams(searchParams.toString())) }))}>{result.title}</Link>
                ))}
              </div>
            </div>
          </aside>
        )}
        </div>}
      </div>
    </div>
  )
}

export default function GraphPage() {
  return <Suspense fallback={null}><GraphContent /></Suspense>
}
