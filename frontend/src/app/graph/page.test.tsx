import { cleanup, fireEvent, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../../lib/api'
import { navigationHarness, resetNavigationHarness } from '../../test/navigation-harness'
import { renderWithQuery } from '../../test/render'
import GraphPage from './page'

vi.mock('../../lib/api', async importOriginal => ({ ...(await importOriginal<typeof import('../../lib/api')>()), api: { entityGraph: vi.fn(), edgeEvidence: vi.fn(), search: vi.fn(), searchSources: vi.fn() } }))
// ECharts needs a canvas, so the chart is replaced by a control that emits the clicked node id.
vi.mock('../../components/EntityGraph', () => ({
  EntityGraph: (props: { nodes?: unknown[]; onSelect: (id: string) => void; onSelectEdge: (source: string, target: string) => void }) =>
    <>
      <button type="button" onClick={() => props.onSelect('entity-two')}>{`Chart with ${props.nodes?.length} nodes`}</button>
      <button type="button" onClick={() => props.onSelectEdge('entity-two', 'entity-one')}>Chart edge</button>
    </>,
}))

const nodes = [
  { id: 'entity-one', text: 'Acme', type: 'ORG', article_count: 4 },
  { id: 'entity-two', text: 'Jane Doe', type: 'PERSON', article_count: 2 },
]
const edges = [{ source: 'entity-one', target: 'entity-two', weight: 3 }]

beforeEach(() => {
  vi.clearAllMocks()
  resetNavigationHarness({ pathname: '/graph/' })
  vi.mocked(api.entityGraph).mockResolvedValue({ nodes, edges, truncated: false })
  vi.mocked(api.edgeEvidence).mockResolvedValue({
    source: { id: 'entity-one', text: 'Acme', type: 'ORG' }, target: { id: 'entity-two', text: 'Jane Doe', type: 'PERSON' },
    meaning: 'Both entities are mentioned in the same article. This is co-occurrence, not a stated relationship.',
    article_count: 3, cluster_count: 1, first_at: null, last_at: null, articles: [], next_cursor: null, clusters: [], missing_from_archive: 0,
  })
  vi.mocked(api.search).mockResolvedValue({ items: [{ article_id: 'a1', title: 'Acme partners with Jane Doe', effective_date: '2026-09-14T12:00:00Z', distinct_source_count: 1, sources: ['Wire'], source_refs: [], highlights: [], summary: null }], next_cursor: null })
  vi.mocked(api.searchSources).mockResolvedValue({ items: [{ id: 's1', name: 'Wire', source_country: 'US', retired: false }], next_cursor: null })
})
afterEach(cleanup)

it('restores filters from the URL and requests the bounded graph', async () => {
  resetNavigationHarness({ pathname: '/graph/', search: 'q=grid&country=US&nodes=40' })
  renderWithQuery(() => <GraphPage />)

  await screen.findByText('Chart with 2 nodes')
  expect(api.entityGraph).toHaveBeenLastCalledWith({ q: 'grid', source_country: ['US'], nodes: '40' })
})

it('clamps a hand-edited node count to the backend maximum of 50', async () => {
  resetNavigationHarness({ pathname: '/graph/', search: 'nodes=500' })
  renderWithQuery(() => <GraphPage />)

  await screen.findByText('Chart with 2 nodes')
  expect(api.entityGraph).toHaveBeenLastCalledWith({ nodes: '50' })
})

it('commits filter changes to the URL and refetches', async () => {
  const { rerenderSame } = renderWithQuery(() => <GraphPage />)
  await screen.findByText('Chart with 2 nodes')

  fireEvent.change(screen.getByLabelText('Query'), { target: { value: 'climate' } })
  await fireEvent.submit(screen.getByRole('search'))
  rerenderSame()

  expect(navigationHarness.searchParams.get('q')).toBe('climate')
  await vi.waitFor(() => expect(api.entityGraph).toHaveBeenLastCalledWith(expect.objectContaining({ q: 'climate' })))
})

it('selects a node from the accessible fallback list and shows its side panel', async () => {
  const { rerenderSame } = renderWithQuery(() => <GraphPage />)
  await screen.findByText('Chart with 2 nodes')

  fireEvent.click(screen.getByRole('button', { name: /Acme \(ORG\)/ }))
  rerenderSame()

  expect(navigationHarness.searchParams.get('focus')).toBe('entity-one')
  expect(await screen.findByRole('heading', { name: 'Acme' })).toBeTruthy()
  const panel = screen.getByRole('complementary', { name: 'Entity details' })
  expect(panel.textContent).toContain('4 articles')
  expect(await screen.findByText('Acme partners with Jane Doe')).toBeTruthy()
})

it('refocuses the graph when a connected entity is clicked', async () => {
  resetNavigationHarness({ pathname: '/graph/', search: 'focus=entity-one' })
  const { rerenderSame } = renderWithQuery(() => <GraphPage />)
  await screen.findByText('Chart with 2 nodes')

  fireEvent.click(await screen.findByRole('button', { name: 'Jane Doe' }))
  rerenderSame()

  expect(navigationHarness.searchParams.get('focus')).toBe('entity-two')
  await vi.waitFor(() => expect(api.entityGraph).toHaveBeenLastCalledWith(expect.objectContaining({ focus_entity_id: 'entity-two' })))
})

it('reacts to the mocked chart emitting a selection', async () => {
  renderWithQuery(() => <GraphPage />)
  fireEvent.click(await screen.findByText('Chart with 2 nodes'))

  expect(navigationHarness.searchParams.get('focus')).toBe('entity-two')
})

it('links from the side panel to search with this entity', async () => {
  resetNavigationHarness({ pathname: '/graph/', search: 'focus=entity-one' })
  renderWithQuery(() => <GraphPage />)
  await screen.findByRole('heading', { name: 'Acme' })

  expect(screen.getByRole('link', { name: 'Search articles with Acme' })).toHaveAttribute('href', '/search/?entity_id=entity-one')
  expect(screen.getByRole('link', { name: 'Open dossier for Acme' })).toHaveAttribute('href', '/entities/?id=entity-one')
})

it('shows the empty state when there are no co-occurring entities', async () => {
  vi.mocked(api.entityGraph).mockResolvedValue({ nodes: [], edges: [], truncated: false })
  const { rerenderSame } = renderWithQuery(() => <GraphPage />)

  expect(await screen.findByText('No co-occurring entities yet.')).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Clear all filters' })).toBeNull()

  navigationHarness.push('/graph/?story_country=DE&nodes=40')
  rerenderSame()
  expect(await screen.findByText('No co-occurring entities for these filters.')).toBeTruthy()
  fireEvent.click(screen.getAllByRole('button', { name: 'Clear all filters' })[0])
  expect(navigationHarness.pathname + '?' + navigationHarness.searchParams).toBe('/graph/?nodes=40')
})

it('shows an upgrade message when the search index needs a rebuild', async () => {
  vi.mocked(api.entityGraph).mockRejectedValue(new ApiError('Rebuild search to schema version 2 to use the entity graph', 409, { code: 'search_upgrade_required' }))
  renderWithQuery(() => <GraphPage />)

  expect(await screen.findByText('Search upgrade required. Rebuild the search index to use the entity graph.')).toBeTruthy()
})

it('lists each connection as a button and opens its evidence with a canonical edge param', async () => {
  const { rerenderSame } = renderWithQuery(() => <GraphPage />)
  await screen.findByText('Chart with 2 nodes')

  fireEvent.click(screen.getByRole('button', { name: 'Acme — Jane Doe · 3' }))
  rerenderSame()

  expect(navigationHarness.searchParams.get('edge')).toBe('entity-one:entity-two')
  expect(await screen.findByRole('complementary', { name: 'Relationship evidence' })).toBeTruthy()
  expect(await screen.findByRole('heading', { name: 'Acme and Jane Doe' })).toBeTruthy()
})

it('canonicalises an edge chosen from the chart in either direction', async () => {
  renderWithQuery(() => <GraphPage />)

  fireEvent.click(await screen.findByRole('button', { name: 'Chart edge' }))

  expect(navigationHarness.searchParams.get('edge')).toBe('entity-one:entity-two')
})

it('requests evidence with the graph filters and focus but not the node count', async () => {
  resetNavigationHarness({ pathname: '/graph/', search: 'q=grid&country=US&nodes=40&focus=entity-one&edge=entity-one%3Aentity-two' })
  renderWithQuery(() => <GraphPage />)

  await screen.findByRole('heading', { name: 'Acme and Jane Doe' })
  expect(api.edgeEvidence).toHaveBeenLastCalledWith({ q: 'grid', source_country: ['US'], focus_entity_id: 'entity-one', source: 'entity-one', target: 'entity-two' }, undefined)
})

it('closes the evidence when another entity is selected and ignores a malformed edge param', async () => {
  resetNavigationHarness({ pathname: '/graph/', search: 'edge=entity-one%3Aentity-two' })
  const { rerenderSame } = renderWithQuery(() => <GraphPage />)
  await screen.findByRole('heading', { name: 'Acme and Jane Doe' })

  fireEvent.click(screen.getByRole('button', { name: /Jane Doe \(PERSON\)/ }))
  rerenderSame()
  expect(navigationHarness.searchParams.get('edge')).toBeNull()
  expect(navigationHarness.searchParams.get('focus')).toBe('entity-two')
  cleanup()

  vi.mocked(api.edgeEvidence).mockClear()
  resetNavigationHarness({ pathname: '/graph/', search: 'edge=bogus' })
  renderWithQuery(() => <GraphPage />)
  await screen.findByText('Chart with 2 nodes')
  expect(screen.queryByRole('complementary', { name: 'Relationship evidence' })).toBeNull()
  expect(api.edgeEvidence).not.toHaveBeenCalled()
})

it('chips only the criteria the graph sends, and removing one drops the selected edge but keeps focus and nodes', async () => {
  const { rerenderSame } = renderWithQuery(() => <GraphPage />)
  navigationHarness.push('/graph/?q=grid&source_id=s1&story_country=DE&entity_id=elsewhere&focus=entity-one&edge=entity-one:entity-two&nodes=40')
  rerenderSame()
  const bar = await screen.findByRole('region', { name: 'Applied filters' })
  await vi.waitFor(() => expect(within(bar).getByRole('button', { name: 'Remove Source: Wire' })).toBeTruthy())
  expect(await within(bar).findByRole('button', { name: 'Remove Focused entity: Acme' })).toBeTruthy()
  expect(within(bar).queryByRole('button', { name: /elsewhere/ })).toBeNull()
  expect((screen.getByText(/^Advanced filters/).closest('details') as HTMLDetailsElement).open).toBe(true)

  fireEvent.click(within(bar).getByRole('button', { name: 'Remove Story country: DE' }))
  rerenderSame()
  expect(navigationHarness.searchParams.get('edge')).toBeNull()
  expect(navigationHarness.searchParams.get('focus')).toBe('entity-one')
  expect(navigationHarness.searchParams.get('nodes')).toBe('40')
  expect(navigationHarness.searchParams.getAll('source_id')).toEqual(['s1'])
  expect(navigationHarness.searchParams.get('story_country')).toBeNull()

  fireEvent.click(screen.getByRole('button', { name: /^Remove Focused entity: / }))
  rerenderSame()
  expect(navigationHarness.searchParams.get('focus')).toBeNull()
  expect(navigationHarness.searchParams.get('q')).toBe('grid')
})

it('counts a changed node count as an unapplied edit', async () => {
  resetNavigationHarness({ pathname: '/graph/', search: 'q=grid' })
  renderWithQuery(() => <GraphPage />)
  expect(screen.queryByText(/unapplied changes/)).toBeNull()
  await screen.findByRole('region', { name: 'Applied filters' })
  fireEvent.change(screen.getByLabelText('Nodes'), { target: { value: '12' } })
  expect(screen.getByText('You have unapplied changes. Filter-chip actions discard them.')).toBeTruthy()
})

it('offers Retry when the graph is temporarily unavailable', async () => {
  vi.mocked(api.entityGraph).mockRejectedValueOnce(new ApiError('down', 503))
  renderWithQuery(() => <GraphPage />)
  expect(await screen.findByText('The entity graph is temporarily unavailable.')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  expect(await screen.findByRole('button', { name: 'Chart with 2 nodes' })).toBeTruthy()
})
