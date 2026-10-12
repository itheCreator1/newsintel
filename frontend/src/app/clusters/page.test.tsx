import { cleanup, fireEvent, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '../../lib/api'
import { navigationHarness, resetNavigationHarness } from '../../test/navigation-harness'
import { renderWithQuery } from '../../test/render'
import ClustersPage from './page'

vi.mock('../../lib/api', async importOriginal => ({ ...(await importOriginal<typeof import('../../lib/api')>()), api: { cluster: vi.fn(), clusterSummary: vi.fn(), createMonitor: vi.fn() } }))
// ECharts needs a canvas, so the chart is replaced by a control that emits a brushed span.
vi.mock('../../components/TimelineChart', () => ({
  TimelineChart: (props: { buckets?: unknown[]; interval?: string; onSelect: (range: { start: string; end: string }) => void }) =>
    <button type="button" onClick={() => props.onSelect({ start: '2026-09-10T08:00:00Z', end: '2026-09-10T10:00:00Z' })}>{`Brush ${props.buckets?.length} ${props.interval} buckets`}</button>,
}))

const page = (items: { article_id: string; title: string }[], next: string | null) => ({
  id: 'cluster-1', algorithm_version: 'v1', article_count: 3, source_count: 2, first_published_at: '2026-09-10T08:00:00Z', last_published_at: '2026-09-12T18:00:00Z', representative_article_id: 'a1',
  members: { items: items.map(item => ({ ...item, effective_date: '2026-09-11T00:00:00Z', feeds: [{ id: 'feed-wire', name: 'Wire' }], score: 0.9 })), next_cursor: next },
})

beforeEach(() => {
  vi.clearAllMocks()
  resetNavigationHarness({ pathname: '/clusters/', search: 'id=cluster-1' })
  vi.mocked(api.cluster).mockResolvedValue(page([{ article_id: 'a1', title: 'First report' }], null))
  vi.mocked(api.clusterSummary).mockResolvedValue(summary)
})

const summary = {
  id: 'cluster-1', title: 'Harbour fire spreads to the port',
  entities: [{ id: 'e-port', display_name: 'Port Authority', entity_type: 'ORG', article_count: 3 }, { id: 'e-piraeus', display_name: 'Piraeus', entity_type: 'GPE', article_count: 2 }],
  event: { id: 'ev-1', headline: 'Fires across Attica' },
  timeline: { interval: 'hour' as const, buckets: [{ start: '2026-09-10T08:00:00Z', count: 1 }, { start: '2026-09-10T09:00:00Z', count: 0 }, { start: '2026-09-10T10:00:00Z', count: 2 }] },
  sources: [
    { id: 'feed-first', name: 'Harbour Wire', first_reported_at: '2026-09-10T08:00:00Z', minutes_behind: 0 },
    { id: 'feed-second', name: 'Daily', first_reported_at: '2026-09-10T10:30:00Z', minutes_behind: 150 },
    { id: 'feed-late', name: 'Weekly', first_reported_at: '2026-09-11T09:00:00Z', minutes_behind: 1500 },
  ],
}
afterEach(cleanup)

it('shows the story header with counts and time span', async () => {
  renderWithQuery(() => <ClustersPage />)

  expect(await screen.findByText('3 articles · 2 sources')).toBeTruthy()
  expect(screen.getByText(new RegExp(new Date('2026-09-10T08:00:00Z').toLocaleString().replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))).toBeTruthy()
  expect(api.cluster).toHaveBeenCalledWith('cluster-1', undefined)
})

it('loads more members without replacing earlier ones and opens an article with a return path', async () => {
  vi.mocked(api.cluster)
    .mockResolvedValueOnce(page([{ article_id: 'a1', title: 'First report' }], 'next'))
    .mockResolvedValueOnce(page([{ article_id: 'a2', title: 'Second report' }], null))
  renderWithQuery(() => <ClustersPage />)

  await screen.findByText('First report')
  await fireEvent.click(screen.getByRole('button', { name: 'Load more' }))

  expect(await screen.findByText('Second report')).toBeTruthy()
  expect(screen.getByText('First report')).toBeTruthy()
  expect(api.cluster).toHaveBeenLastCalledWith('cluster-1', 'next')

  fireEvent.click(screen.getByRole('button', { name: /First report/ }))
  expect(navigationHarness.push).toHaveBeenCalledWith('/articles/?article=a1&from=%2Fclusters%2F%3Fid%3Dcluster-1')
})

it('links to search within this story, preserving the originating investigation', async () => {
  resetNavigationHarness({ pathname: '/clusters/', search: 'id=cluster-1&from=%2Fsearch%3Fq%3Dgrid%26country%3DUS' })
  renderWithQuery(() => <ClustersPage />)

  await screen.findByText('First report')

  expect(screen.getByRole('link', { name: 'Search within this story' })).toHaveAttribute('href', '/search/?q=grid&country=US&story_cluster_id=cluster-1')
})

it('shows an error state when the cluster cannot be loaded', async () => {
  vi.mocked(api.cluster).mockRejectedValue(new Error('boom'))
  renderWithQuery(() => <ClustersPage />)

  expect(await screen.findByText('Could not load this story.')).toBeTruthy()
})

it('watches this story, named after its first article', async () => {
  vi.mocked(api.createMonitor).mockResolvedValueOnce({} as never)
  renderWithQuery(() => <ClustersPage />)

  fireEvent.click(await screen.findByRole('button', { name: 'Watch' }))
  fireEvent.click(screen.getByRole('button', { name: 'Watch story' }))
  await vi.waitFor(() => expect(api.createMonitor).toHaveBeenCalledWith('First report', expect.objectContaining({ story_cluster_id: ['cluster-1'], q: '' }), 'cluster'))
})

it('names the story by its representative article and shows its entities, event and first reports', async () => {
  renderWithQuery(() => <ClustersPage />)

  expect(await screen.findByRole('heading', { name: 'Harbour fire spreads to the port' })).toBeTruthy()
  expect(api.clusterSummary).toHaveBeenCalledWith('cluster-1')
  expect(screen.getByRole('link', { name: 'Port Authority (3)' })).toHaveAttribute('href', '/entities/?id=e-port')
  expect(screen.getByRole('link', { name: 'Fires across Attica' })).toHaveAttribute('href', '/events/detail/?id=ev-1&from=%2Fclusters%2F%3Fid%3Dcluster-1')
  expect(screen.getByText(/First reported by/)).toHaveTextContent('First reported by Harbour Wire')

  const reports = screen.getByRole('table', { name: 'Who reported it when' })
  const rows = within(reports).getAllByRole('row').slice(1).map(row => within(row).getAllByRole('cell').map(cell => cell.textContent))
  expect(rows.map(row => [row[0], row[2]])).toEqual([['Harbour Wire', 'First'], ['Daily', '+2 h 30 min'], ['Weekly', '+1 d 1 h']])
  expect(within(reports).getByRole('link', { name: 'Daily' })).toHaveAttribute('href', '/sources/detail/?id=feed-second&from=%2Fclusters%2F%3Fid%3Dcluster-1')
})

it('charts the story by hour and searches the brushed hours within it', async () => {
  renderWithQuery(() => <ClustersPage />)

  fireEvent.click(await screen.findByRole('button', { name: 'Brush 3 hour buckets' }))
  expect(navigationHarness.push).toHaveBeenCalledWith('/search/?after=2026-09-10&before=2026-09-11&story_cluster_id=cluster-1')
})

it('keeps the member list when the summary cannot be loaded', async () => {
  vi.mocked(api.clusterSummary).mockRejectedValue(new Error('boom'))
  renderWithQuery(() => <ClustersPage />)

  expect(await screen.findByText('First report')).toBeTruthy()
  expect(await screen.findByText('Could not load the story summary.')).toBeTruthy()
  expect(screen.getByRole('heading', { name: 'Story' })).toBeTruthy()
})
