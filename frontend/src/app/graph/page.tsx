'use client'

import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useEffect, useState } from 'react'
import { EntityGraph } from '../../components/EntityGraph'
import { api, ApiError } from '../../lib/api'
import { queryFromState, refine, stateFromQuery, toHref, type Investigation } from '../../lib/investigation'

const MAX_NODES = 50
const split = (value: string) => value.split(/[\s,]+/).filter(Boolean)

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
  const nodeCount = (() => {
    const raw = Number(searchParams.get('nodes'))
    // A hand-edited URL or bookmark can carry any value; the backend rejects anything over MAX_NODES with a 422.
    return Number.isFinite(raw) && raw > 0 ? Math.min(Math.floor(raw), MAX_NODES) : 30
  })()
  const [form, setForm] = useState(() => formFromState(state, nodeCount))
  const [sourceTerm, setSourceTerm] = useState('')

  useEffect(() => { setForm(formFromState(state, nodeCount)) /* eslint-disable-line react-hooks/exhaustive-deps */ }, [searchParams.toString()])

  const sourcePages = useInfiniteQuery({ queryKey: ['graph-sources', sourceTerm], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam }) => api.searchSources(sourceTerm, pageParam), getNextPageParam: page => page.next_cursor ?? undefined })
  const sources = sourcePages.data?.pages.flatMap(page => page.items) ?? []

  // Only the fields the graph filter form exposes are sent: the graph endpoint accepts more (entity_id,
  // keyword_id, story_cluster_id, ...) via the shared search criteria, but leaking whatever happens to be
  // in the URL from another view would silently change results the user never asked to filter by.
  const graphFilters: Record<string, string | string[] | undefined> = {}
  if (state.q) graphFilters.q = state.q
  if (state.source_id.length) graphFilters.source_id = state.source_id
  if (state.source_country.length) graphFilters.source_country = state.source_country
  if (state.story_country.length) graphFilters.story_country = state.story_country
  if (state.entity_type.length) graphFilters.entity_type = state.entity_type
  if (state.after) graphFilters.after = state.after
  if (state.before) graphFilters.before = state.before
  graphFilters.nodes = String(nodeCount)
  if (focus) graphFilters.focus_entity_id = focus

  const graph = useQuery({ queryKey: ['entity-graph', graphFilters], queryFn: () => api.entityGraph(graphFilters), retry: false })
  const nodes = graph.data?.nodes ?? []
  const edges = graph.data?.edges ?? []
  const focusNode = nodes.find(node => node.id === focus) ?? null
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

  function navigate(next: Investigation, extra: { focus?: string; nodes?: number } = {}) {
    const query = queryFromState(next)
    const nextFocus = extra.focus !== undefined ? extra.focus : focus
    const nextNodes = Math.min(extra.nodes !== undefined ? extra.nodes : nodeCount, MAX_NODES)
    if (nextFocus) query.set('focus', nextFocus)
    if (nextNodes !== 30) query.set('nodes', String(nextNodes))
    router.push(toHref('/graph', query))
  }
  function submit() {
    navigate({
      ...state, q: form.q.trim(), source_id: form.source_id, source_country: split(form.country).map(code => code.toUpperCase()),
      story_country: split(form.story_country).map(code => code.toUpperCase()), entity_type: form.entity_type,
      after: form.after || null, before: form.before || null,
    }, { nodes: Number(form.nodes) || 30 })
  }
  function selectEntity(entityId: string) { navigate(state, { focus: entityId }) }
  function searchWithEntityHref(entityId: string) { return toHref('/search', queryFromState(refine(state, 'entity_id', entityId))) }
  const selected = (event: React.ChangeEvent<HTMLSelectElement>) => Array.from(event.target.selectedOptions, option => option.value)

  return (
    <>
      <header><div><p className="eyebrow">Relationships</p><h2>Graph</h2></div></header>
      <form className="search-filters panel" role="search" onSubmit={event => { event.preventDefault(); submit() }}>
        <label className="wide">Query<input value={form.q} onChange={e => setForm(f => ({ ...f, q: e.target.value }))} placeholder='climate AND "sea level"' /></label>
        <label>Source search<input value={sourceTerm} onChange={e => setSourceTerm(e.target.value)} placeholder="Find active or retired sources" /></label>
        <label>Source<select multiple value={form.source_id} onChange={e => setForm(f => ({ ...f, source_id: selected(e) }))}>{sources.map(source => <option key={source.id} value={source.id}>{source.name}{source.retired ? ' (retired)' : ''}</option>)}</select></label>
        <label>Source country<input value={form.country} onChange={e => setForm(f => ({ ...f, country: e.target.value }))} placeholder="US, GR" /></label>
        <label>Story country<input value={form.story_country} onChange={e => setForm(f => ({ ...f, story_country: e.target.value }))} placeholder="DE" /></label>
        <label>Entity type<select multiple value={form.entity_type} onChange={e => setForm(f => ({ ...f, entity_type: selected(e) }))}>{['PERSON', 'ORG', 'GPE', 'COUNTRY', 'LOCATION', 'EVENT', 'PRODUCT', 'OTHER'].map(kind => <option key={kind}>{kind}</option>)}</select></label>
        <label>After<input value={form.after} onChange={e => setForm(f => ({ ...f, after: e.target.value }))} type="date" /></label>
        <label>Before<input value={form.before} onChange={e => setForm(f => ({ ...f, before: e.target.value }))} type="date" /></label>
        <label>Nodes<input value={form.nodes} onChange={e => setForm(f => ({ ...f, nodes: e.target.value }))} type="number" min={1} max={50} /></label>
        <button type="submit">Update graph</button>
      </form>
      {/* .two-column reserves a second ~1.25fr grid track even when nothing occupies it — only apply it
          once the entity-details aside actually renders, so the graph gets the full width otherwise. */}
      <div className={focusNode ? 'two-column' : undefined}>
        <section className="panel graph-panel">
          {graph.isPending && <p className="muted">Loading the entity graph…</p>}
          {!graph.isPending && graph.isError && <p role="alert" className="error">{upgradeRequired ? 'Search upgrade required. Rebuild the search index to use the entity graph.' : graphError?.status === 503 ? 'The entity graph is temporarily unavailable.' : 'Could not load the entity graph.'}</p>}
          {!graph.isPending && !graph.isError && !nodes.length && <p className="muted">No co-occurring entities for these filters.</p>}
          {!graph.isPending && !graph.isError && nodes.length > 0 && (
            <>
              <EntityGraph nodes={nodes} edges={edges} focus={focus} onSelect={selectEntity} />
              {graph.data?.truncated && <p className="muted">Showing a bounded subset of the graph. Narrow the filters to see more.</p>}
              <ul className="graph-node-list" aria-label="Entities in this graph">
                {nodes.map(node => <li key={node.id}><button type="button" className="annotation-link" aria-pressed={node.id === focus} onClick={() => selectEntity(node.id)}>{node.text} ({node.type}) · {node.article_count}</button></li>)}
              </ul>
            </>
          )}
        </section>
        {focusNode && (
          <aside className="panel" aria-label="Entity details">
            <p className="eyebrow">{focusNode.type}</p>
            <h3>{focusNode.text}</h3>
            <p className="muted">{focusNode.article_count} articles</p>
            <Link className="annotation-link" href={searchWithEntityHref(focusNode.id)}>Search articles with {focusNode.text}</Link>
            <div className="annotation-group">
              <strong>Connected entities</strong>
              {connected.map(node => <button key={node.id} type="button" className="annotation-link" onClick={() => selectEntity(node.id)}>{node.text}</button>)}
              {!connected.length && 'None'}
            </div>
            <div className="annotation-group">
              <strong>Top articles</strong>
              {articles.isPending && <p className="muted">Loading articles…</p>}
              {!articles.isPending && articles.isError && <p className="error">Could not load articles.</p>}
              {!articles.isPending && !articles.isError && !articles.data?.items.length && <p className="muted">No matching articles.</p>}
              {(articles.data?.items ?? []).map(result => (
                <Link key={result.article_id} className="annotation-link" href={toHref('/articles', new URLSearchParams({ article: result.article_id, from: toHref('/graph', new URLSearchParams(searchParams.toString())) }))}>{result.title}</Link>
              ))}
            </div>
          </aside>
        )}
      </div>
    </>
  )
}

export default function GraphPage() {
  return <Suspense fallback={null}><GraphContent /></Suspense>
}
