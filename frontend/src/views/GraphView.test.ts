import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../api'
import GraphView from './GraphView.vue'

vi.mock('../api', async importOriginal => ({ ...(await importOriginal<typeof import('../api')>()), api: { entityGraph: vi.fn(), search: vi.fn() } }))
// ECharts needs a canvas, so the chart is replaced by a control that emits the clicked node id.
vi.mock('../components/EntityGraph.vue', async () => { const { defineComponent, h } = await import('vue'); return { default: defineComponent({ props: { nodes: Array, edges: Array, focus: String }, emits: ['select'], setup: (props, { emit }) => () => h('button', { type: 'button', onClick: () => emit('select', 'entity-two') }, `Chart with ${props.nodes?.length} nodes`) }) } })

const nodes = [
  { id: 'entity-one', text: 'Acme', type: 'ORG', article_count: 4 },
  { id: 'entity-two', text: 'Jane Doe', type: 'PERSON', article_count: 2 },
]
const edges = [{ source: 'entity-one', target: 'entity-two', weight: 3 }]

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.entityGraph).mockResolvedValue({ nodes, edges, truncated: false })
  vi.mocked(api.search).mockResolvedValue({ items: [{ article_id: 'a1', title: 'Acme partners with Jane Doe', effective_date: '2026-09-14T12:00:00Z', distinct_source_count: 1, sources: ['Wire'], source_refs: [], highlights: [], summary: null }], next_cursor: null })
})
afterEach(cleanup)

async function renderGraph(url = '/graph') {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/graph', component: GraphView }, { path: '/search', component: { template: '<div />' } }, { path: '/articles', component: { template: '<div />' } },
  ] })
  await router.push(url); await router.isReady()
  return { router, ...render(GraphView, { global: { plugins: [[VueQueryPlugin, { queryClient: new QueryClient({ defaultOptions: { queries: { retry: false } } }) }], router] } }) }
}

it('restores filters from the URL and requests the bounded graph', async () => {
  await renderGraph('/graph?q=grid&country=US&nodes=40')

  await screen.findByText('Chart with 2 nodes')
  expect(api.entityGraph).toHaveBeenLastCalledWith({ q: 'grid', source_country: ['US'], nodes: '40' })
})

it('clamps a hand-edited node count to the backend maximum of 50', async () => {
  await renderGraph('/graph?nodes=500')

  await screen.findByText('Chart with 2 nodes')
  expect(api.entityGraph).toHaveBeenLastCalledWith({ nodes: '50' })
})

it('commits filter changes to the URL and refetches', async () => {
  const { router } = await renderGraph()
  await screen.findByText('Chart with 2 nodes')

  await fireEvent.update(screen.getByLabelText('Query'), 'climate')
  await fireEvent.submit(screen.getByRole('search'))

  await vi.waitFor(() => expect(router.currentRoute.value.query.q).toBe('climate'))
  expect(api.entityGraph).toHaveBeenLastCalledWith(expect.objectContaining({ q: 'climate' }))
})

it('selects a node from the accessible fallback list and shows its side panel', async () => {
  const { router } = await renderGraph()
  await screen.findByText('Chart with 2 nodes')

  await fireEvent.click(screen.getByRole('button', { name: /Acme \(ORG\)/ }))

  await vi.waitFor(() => expect(router.currentRoute.value.query.focus).toBe('entity-one'))
  expect(await screen.findByRole('heading', { name: 'Acme' })).toBeTruthy()
  const panel = screen.getByRole('complementary', { name: 'Entity details' })
  expect(panel.textContent).toContain('4 articles')
  expect(await screen.findByText('Acme partners with Jane Doe')).toBeTruthy()
})

it('refocuses the graph when a connected entity is clicked', async () => {
  const { router } = await renderGraph('/graph?focus=entity-one')
  await screen.findByText('Chart with 2 nodes')

  await fireEvent.click(await screen.findByRole('button', { name: 'Jane Doe' }))

  await vi.waitFor(() => expect(router.currentRoute.value.query.focus).toBe('entity-two'))
  expect(api.entityGraph).toHaveBeenLastCalledWith(expect.objectContaining({ focus_entity_id: 'entity-two' }))
})

it('reacts to the mocked chart emitting a selection', async () => {
  const { router } = await renderGraph()
  await fireEvent.click(await screen.findByText('Chart with 2 nodes'))

  await vi.waitFor(() => expect(router.currentRoute.value.query.focus).toBe('entity-two'))
})

it('links from the side panel to search with this entity', async () => {
  const { router } = await renderGraph('/graph?focus=entity-one')
  await screen.findByRole('heading', { name: 'Acme' })

  await fireEvent.click(screen.getByRole('link', { name: 'Search articles with Acme' }))

  await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/search'))
  expect(router.currentRoute.value.query).toEqual({ entity_id: ['entity-one'] })
})

it('shows the empty state when there are no co-occurring entities', async () => {
  vi.mocked(api.entityGraph).mockResolvedValue({ nodes: [], edges: [], truncated: false })
  await renderGraph()

  expect(await screen.findByText('No co-occurring entities for these filters.')).toBeTruthy()
})

it('shows an upgrade message when the search index needs a rebuild', async () => {
  vi.mocked(api.entityGraph).mockRejectedValue(new ApiError('Rebuild search to schema version 2 to use the entity graph', 409, { code: 'search_upgrade_required' }))
  await renderGraph()

  expect(await screen.findByText('Search upgrade required. Rebuild the search index to use the entity graph.')).toBeTruthy()
})
