'use client'

import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useEffect, useState } from 'react'
import { EdgeEvidencePanel } from '../../components/EdgeEvidencePanel'
import { ActiveFilterBar } from '../../components/ActiveFilterBar'
import { EntityGraph, type ColourBy } from '../../components/EntityGraph'
import { EmptyState, ErrorNotice, LoadingState } from '../../components/Feedback'
import { GlassPanel, glassPanelClassName } from '../../components/GlassPanel'
import { PageHeader } from '../../components/PageHeader'
import { chipClass, fieldClass, ghostButtonClass, labelClass, primaryButtonClass } from '../../lib/ui-classes'
import { advancedCount, errorCode, filterChips, GRAPH_ADVANCED, GRAPH_CHIP_FIELDS, isTransient, removeFilter } from '../../lib/filter-ui'
import { cn } from '../../lib/utils'
import { isNewEdge } from '../../lib/graph-edges'
import { statedText } from '../../lib/see-also'
import { api, ApiError } from '../../lib/api'
import { emptyInvestigation, entityHref, queryFromState, refine, stateFromQuery, toHref, type Investigation } from '../../lib/investigation'

const MAX_NODES = 50
/** The backend draws at most this many expansions at once. */
const MAX_EXPANDED = 5
const DEFAULT_MIN_WEIGHT = 2
/** Without a selected entity, the connection list shows the strongest few until asked for all. */
const LISTED_CONNECTIONS = 20
const split = (value: string) => value.split(/[\s,]+/).filter(Boolean)
const edgeKey = (a: string, b: string) => [a, b].sort().join(':')

function formFromState(current: Investigation, nodeCount: number, minWeight: number) {
  return {
    q: current.q, source_id: [...current.source_id], country: current.source_country.join(', '), story_country: current.story_country.join(', '),
    entity_type: [...current.entity_type], after: current.after ?? '', before: current.before ?? '', nodes: String(nodeCount), min_weight: String(minWeight),
  }
}

function GraphContent() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const state = stateFromQuery(searchParams)
  // `focus` narrows the graph to one entity's articles; `selected` only highlights an entity and opens its
  // panel, so picking a node keeps the graph you were looking at.
  const focus = searchParams.get('focus') ?? ''
  const selected = searchParams.get('selected') ?? ''
  const expand = [...new Set(searchParams.getAll('expand').filter(Boolean))].slice(0, MAX_EXPANDED)
  const edge = (() => {
    const [source, target, ...rest] = (searchParams.get('edge') ?? '').split(':')
    return source && target && !rest.length && source !== target ? [source, target] as const : null
  })()
  const nodeCount = (() => {
    const raw = Number(searchParams.get('nodes'))
    // A hand-edited URL or bookmark can carry any value; the backend rejects anything over MAX_NODES with a 422.
    return Number.isFinite(raw) && raw > 0 ? Math.min(Math.floor(raw), MAX_NODES) : 30
  })()
  // Stated see-also links are drawn only when asked for, and the choice is in the URL with the rest.
  const showStated = searchParams.get('stated') === '1'
  const minWeight = (() => {
    const raw = Number(searchParams.get('min_weight'))
    return Number.isFinite(raw) && raw >= 1 ? Math.floor(raw) : DEFAULT_MIN_WEIGHT
  })()
  const [form, setForm] = useState(() => formFromState(state, nodeCount, minWeight))
  const [colourBy, setColourBy] = useState<ColourBy>('type')
  const [allConnections, setAllConnections] = useState(false)
  const [sourceTerm, setSourceTerm] = useState('')
  const advanced = advancedCount(state, GRAPH_ADVANCED)
  const [advancedOpen, setAdvancedOpen] = useState(advanced > 0)

  // Every URL change resyncs the form, and reopens Advanced when it holds criteria.
  useEffect(() => {
    setForm(formFromState(state, nodeCount, minWeight))
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
  // Edge evidence takes every graph filter except the drawing options (node count, expansions, minimum
  // weight), so its totals equal the drawn edge weight.
  const graphFilters: Record<string, string | string[] | undefined> = { ...evidenceFilters, nodes: String(nodeCount) }
  if (expand.length) graphFilters.expand = expand
  if (minWeight !== DEFAULT_MIN_WEIGHT) graphFilters.min_edge_weight = String(minWeight)
  if (showStated) graphFilters.stated = 'true'

  const graph = useQuery({ queryKey: ['entity-graph', graphFilters], queryFn: () => api.entityGraph(graphFilters), retry: false })
  const nodes = graph.data?.nodes ?? []
  const edges = graph.data?.edges ?? []
  const stated = showStated ? graph.data?.stated_edges ?? [] : []
  const nodeById = new Map(nodes.map(node => [node.id, node]))
  const focusNode = nodeById.get(focus) ?? null
  // The panel follows the selected entity, or the focused one when nothing else is selected.
  const panelNode = nodeById.get(selected) ?? focusNode
  const selectedEdge = edge ? edgeKey(...edge) : ''
  const panelEdges = panelNode ? edges.filter(link => link.source === panelNode.id || link.target === panelNode.id) : []
  const connectedIds = new Set(panelEdges.map(link => link.source === panelNode!.id ? link.target : link.source))
  const connected = nodes.filter(node => connectedIds.has(node.id))
  // With an entity selected the list narrows to its connections; otherwise the strongest come first (the API
  // already orders edges by link strength) and the rest stay one click away.
  const listedEdges = panelNode ? panelEdges : allConnections ? edges : edges.slice(0, LISTED_CONNECTIONS)
  const recentSince = graph.data?.recent_since ?? null
  const newEdges = edges.filter(link => isNewEdge(link, recentSince)).length
  const articleCriteria = panelNode ? {
    q: state.q || undefined, source_country: state.source_country.length ? state.source_country : undefined,
    story_country: state.story_country.length ? state.story_country : undefined, after: state.after ?? undefined,
    before: state.before ?? undefined, entity_id: [panelNode.id],
  } : null
  const articles = useQuery({ queryKey: ['entity-graph-articles', articleCriteria], queryFn: () => api.search(articleCriteria!), enabled: Boolean(articleCriteria), retry: false })
  const graphError = graph.error instanceof ApiError ? graph.error : null
  const upgradeRequired = graphError?.status === 409 && errorCode(graphError) === 'search_upgrade_required'

  function navigate(next: Investigation, extra: { focus?: string; selected?: string; expand?: string[]; nodes?: number; minWeight?: number; edge?: string; stated?: boolean } = {}) {
    const query = queryFromState(next)
    const nextFocus = extra.focus !== undefined ? extra.focus : focus
    const nextSelected = extra.selected !== undefined ? extra.selected : selected
    const nextExpand = extra.expand !== undefined ? extra.expand : expand
    const nextNodes = Math.min(extra.nodes !== undefined ? extra.nodes : nodeCount, MAX_NODES)
    const nextMinWeight = extra.minWeight !== undefined ? extra.minWeight : minWeight
    if (nextFocus) query.set('focus', nextFocus)
    if (nextSelected) query.set('selected', nextSelected)
    for (const entityId of nextExpand.slice(0, MAX_EXPANDED)) query.append('expand', entityId)
    if (extra.edge) query.set('edge', extra.edge)
    if (nextNodes !== 30) query.set('nodes', String(nextNodes))
    if (nextMinWeight !== DEFAULT_MIN_WEIGHT) query.set('min_weight', String(nextMinWeight))
    if (extra.stated !== undefined ? extra.stated : showStated) query.set('stated', '1')
    router.push(toHref('/graph', query))
  }
  function draftState(draft = form): Investigation {
    return {
      ...state, q: draft.q.trim(), source_id: draft.source_id, source_country: split(draft.country).map(code => code.toUpperCase()),
      story_country: split(draft.story_country).map(code => code.toUpperCase()), entity_type: draft.entity_type,
      after: draft.after || null, before: draft.before || null,
    }
  }
  const draftMinWeight = (draft: typeof form) => Math.max(1, Math.floor(Number(draft.min_weight)) || DEFAULT_MIN_WEIGHT)
  function submit() { navigate(draftState(), { nodes: Number(form.nodes) || 30, minWeight: draftMinWeight(form) }) }
  const draftKey = (draft: typeof form) => `${queryFromState(draftState(draft))}|${Number(draft.nodes) || 30}|${draftMinWeight(draft)}`
  const draftDiffers = draftKey(form) !== draftKey(formFromState(state, nodeCount, minWeight))
  const chips = [
    ...filterChips(state, GRAPH_CHIP_FIELDS, { source_id: new Map(sources.map(source => [source.id, source.name])) }),
    ...(focus ? [{ key: 'focus', label: `Focused entity: ${focusNode?.text ?? focus}` }] : []),
    ...expand.map(entityId => ({ key: `expand:${entityId}`, label: `Expanded: ${nodeById.get(entityId)?.text ?? entityId}` })),
  ]
  // Any criterion change drops the selected edge (navigate never carries it); only the focus chip drops focus,
  // and only an expansion's own chip drops that expansion.
  function removeChip(key: string) {
    if (key === 'focus') navigate(state, { focus: '' })
    else if (key.startsWith('expand:')) navigate(state, { expand: expand.filter(entityId => `expand:${entityId}` !== key) })
    else navigate(removeFilter(state, key))
  }
  function clearAll() { setSourceTerm(''); navigate(emptyInvestigation(), { focus: '', selected: '', expand: [] }) }
  function selectEntity(entityId: string) { navigate(state, { selected: entityId }) }
  function focusEntity(entityId: string) { navigate(state, { focus: entityId, selected: '' }) }
  function expandEntity(entityId: string) { navigate(state, { expand: [...expand, entityId] }) }
  function selectEdge(source: string, target: string) { navigate(state, { edge: edgeKey(source, target) }) }
  function searchWithEntityHref(entityId: string) { return toHref('/search', queryFromState(refine(state, 'entity_id', entityId))) }
  const selectedValues = (event: React.ChangeEvent<HTMLSelectElement>) => Array.from(event.target.selectedOptions, option => option.value)

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
        <label className={labelClass}>Min shared articles<input className={cn(fieldClass, 'mt-1')} value={form.min_weight} onChange={e => setForm(f => ({ ...f, min_weight: e.target.value }))} type="number" min={1} /></label>
        <button type="submit" className={cn(primaryButtonClass, 'self-end sm:col-span-2 lg:col-span-2 lg:col-start-3')}>Update graph</button>
        {/* Collapsing hides the fields but keeps them mounted, so draft edits survive. */}
        <details className="sm:col-span-2 lg:col-span-4" open={advancedOpen} onToggle={event => setAdvancedOpen(event.currentTarget.open)}>
          <summary className="cursor-pointer text-sm font-medium text-foreground">Advanced filters{advanced > 0 ? ` (${advanced})` : ''}</summary>
          <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <label className={labelClass}>Source search<input className={cn(fieldClass, 'mt-1')} value={sourceTerm} onChange={e => setSourceTerm(e.target.value)} placeholder="Find active or retired sources" /></label>
            <label className={labelClass}>Source<select className={cn(fieldClass, 'mt-1')} multiple value={form.source_id} onChange={e => setForm(f => ({ ...f, source_id: selectedValues(e) }))}>{sources.map(source => <option key={source.id} value={source.id}>{source.name}{source.retired ? ' (retired)' : ''}</option>)}</select></label>
            <label className={labelClass}>Source country<input className={cn(fieldClass, 'mt-1')} value={form.country} onChange={e => setForm(f => ({ ...f, country: e.target.value }))} placeholder="US, GR" /></label>
            <label className={labelClass}>Story country<input className={cn(fieldClass, 'mt-1')} value={form.story_country} onChange={e => setForm(f => ({ ...f, story_country: e.target.value }))} placeholder="DE" /></label>
            <label className={labelClass}>Entity type<select className={cn(fieldClass, 'mt-1')} multiple value={form.entity_type} onChange={e => setForm(f => ({ ...f, entity_type: selectedValues(e) }))}>{['PERSON', 'ORG', 'GPE', 'COUNTRY', 'LOCATION', 'EVENT', 'PRODUCT', 'OTHER'].map(kind => <option key={kind}>{kind}</option>)}</select></label>
          </div>
        </details>
      </form>

      <ActiveFilterBar items={chips} onRemove={removeChip} onClear={clearAll} draftDiffers={draftDiffers} />

      <div className={panelNode || edge ? 'grid gap-6 lg:grid-cols-[minmax(0,1.6fr)_minmax(280px,1fr)]' : undefined}>
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
              <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground" role="group" aria-label="Colour nodes by">
                <span>Colour by</span>
                {([['type', 'Type'], ['group', 'Group']] as const).map(([value, label]) => (
                  <button key={value} type="button" className={cn(chipClass, colourBy === value && 'border-primary/60 bg-primary/12 text-foreground')} aria-pressed={colourBy === value} onClick={() => setColourBy(value)}>{label}</button>
                ))}
                <button type="button" className={cn(chipClass, showStated && 'border-primary/60 bg-primary/12 text-foreground')} aria-pressed={showStated} onClick={() => navigate(state, { stated: !showStated })}>Stated links</button>
              </div>
              <EntityGraph nodes={nodes} edges={edges} stated={stated} focus={panelNode?.id ?? ''} selectedEdge={selectedEdge} colourBy={colourBy} recentSince={recentSince} onSelect={selectEntity} onSelectEdge={selectEdge} />
              <p className="mt-2 text-xs text-muted-foreground">
                Thicker, closer lines link entities that mostly appear together.
                {newEdges > 0 && ` Dashed yellow lines are new: all ${newEdges === 1 ? 'of that connection’s' : 'their'} articles date from the last week.`}
              </p>
              {showStated && (
                <p className="mt-2 text-xs text-muted-foreground">
                  {stated.length ? 'Dotted purple lines are see-also links you stated; they never count as co-occurrence.' : 'No stated see-also links between these entities.'}
                </p>
              )}
              {stated.length > 0 && (
                <ul className="list-none pl-0 mt-2 flex flex-wrap gap-2" aria-label="Stated links in this graph">
                  {stated.map(link => (
                    <li key={`${link.source}:${link.label}:${link.target}`} className={chipClass}>
                      {`${nodeById.get(link.source)?.text ?? link.source} · ${statedText(link)} · ${nodeById.get(link.target)?.text ?? link.target}`}
                    </li>
                  ))}
                </ul>
              )}
              {graph.data?.truncated && <p className="mt-2 text-sm text-muted-foreground">Showing a bounded subset of the graph. Narrow the filters to see more.</p>}
              <ul className="list-none pl-0 mt-4 flex flex-wrap gap-2" aria-label="Entities in this graph">
                {nodes.map(node => (
                  <li key={node.id}>
                    <button
                      type="button"
                      className={cn(chipClass, node.id === panelNode?.id && 'border-primary/60 bg-primary/12 text-foreground')}
                      aria-pressed={node.id === panelNode?.id}
                      onClick={() => selectEntity(node.id)}
                    >
                      {node.text} ({node.type}) · {node.article_count}
                    </button>
                  </li>
                ))}
              </ul>
              {listedEdges.length > 0 && (
                <ul className="list-none pl-0 mt-3 flex flex-wrap gap-2" aria-label="Connections in this graph">
                  {listedEdges.map(link => {
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
              {!panelNode && edges.length > LISTED_CONNECTIONS && (
                <button type="button" className={cn(ghostButtonClass, 'mt-2 w-auto')} onClick={() => setAllConnections(value => !value)}>
                  {allConnections ? `Show the ${LISTED_CONNECTIONS} strongest connections` : `Show all ${edges.length} connections`}
                </button>
              )}
            </>
          )}
        </GlassPanel>
        {(panelNode || edge) && <div className="flex flex-col gap-6">
        {edge && <EdgeEvidencePanel source={edge[0]} target={edge[1]} filters={evidenceFilters} returnHref={toHref('/graph', new URLSearchParams(searchParams.toString()))} />}
        {panelNode && (
          <aside aria-label="Entity details" className={cn(glassPanelClassName, 'flex flex-col gap-3')}>
            <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-primary/80">{panelNode.type}</p>
            <h3 className="text-lg font-semibold text-foreground">{panelNode.text}</h3>
            <p className="text-sm text-muted-foreground">{panelNode.article_count} articles</p>
            <div className="flex flex-wrap gap-2">
              <button type="button" className={cn(ghostButtonClass, 'w-auto')} disabled={expand.includes(panelNode.id) || expand.length >= MAX_EXPANDED} onClick={() => expandEntity(panelNode.id)}>
                {expand.includes(panelNode.id) ? 'Connections added' : 'Add its connections'}
              </button>
              {panelNode.id !== focus && <button type="button" className={cn(ghostButtonClass, 'w-auto')} onClick={() => focusEntity(panelNode.id)}>Only articles with {panelNode.text}</button>}
            </div>
            <Link className={cn(chipClass, 'w-fit')} href={searchWithEntityHref(panelNode.id)}>Search articles with {panelNode.text}</Link>
            <Link className={cn(chipClass, 'w-fit')} href={entityHref(panelNode.id)}>Open dossier for {panelNode.text}</Link>
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
