import { cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '../../lib/api'
import { navigationHarness, resetNavigationHarness } from '../../test/navigation-harness'
import { renderWithQuery } from '../../test/render'
import ClustersPage from './page'

vi.mock('../../lib/api', async importOriginal => ({ ...(await importOriginal<typeof import('../../lib/api')>()), api: { cluster: vi.fn(), createMonitor: vi.fn() } }))

const page = (items: { article_id: string; title: string }[], next: string | null) => ({
  id: 'cluster-1', algorithm_version: 'v1', article_count: 3, source_count: 2, first_published_at: '2026-09-10T08:00:00Z', last_published_at: '2026-09-12T18:00:00Z', representative_article_id: 'a1',
  members: { items: items.map(item => ({ ...item, effective_date: '2026-09-11T00:00:00Z', feeds: [{ id: 'feed-wire', name: 'Wire' }], score: 0.9 })), next_cursor: next },
})

beforeEach(() => {
  vi.clearAllMocks()
  resetNavigationHarness({ pathname: '/clusters/', search: 'id=cluster-1' })
  vi.mocked(api.cluster).mockResolvedValue(page([{ article_id: 'a1', title: 'First report' }], null))
})
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

  fireEvent.click(await screen.findByRole('button', { name: 'Watch story' }))
  await vi.waitFor(() => expect(api.createMonitor).toHaveBeenCalledWith('First report', expect.objectContaining({ story_cluster_id: ['cluster-1'], q: '' }), 'cluster'))
})
