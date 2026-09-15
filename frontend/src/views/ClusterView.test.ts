import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '../api'
import ClusterView from './ClusterView.vue'

vi.mock('../api', async importOriginal => ({ ...(await importOriginal<typeof import('../api')>()), api: { cluster: vi.fn() } }))

const page = (items: { article_id: string; title: string }[], next: string | null) => ({
  id: 'cluster-1', algorithm_version: 'v1', article_count: 3, source_count: 2, first_published_at: '2026-09-10T08:00:00Z', last_published_at: '2026-09-12T18:00:00Z', representative_article_id: 'a1',
  members: { items: items.map(item => ({ ...item, effective_date: '2026-09-11T00:00:00Z', feeds: [{ id: 'feed-wire', name: 'Wire' }], score: 0.9 })), next_cursor: next },
})

beforeEach(() => { vi.clearAllMocks(); vi.mocked(api.cluster).mockResolvedValue(page([{ article_id: 'a1', title: 'First report' }], null)) })
afterEach(cleanup)

async function renderCluster(url = '/clusters/cluster-1') {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/clusters/:id', component: ClusterView }, { path: '/articles', component: { template: '<div />' } }, { path: '/search', component: { template: '<div />' } },
  ] })
  await router.push(url); await router.isReady()
  return { router, ...render(ClusterView, { global: { plugins: [[VueQueryPlugin, { queryClient: new QueryClient({ defaultOptions: { queries: { retry: false } } }) }], router] } }) }
}

it('shows the story header with counts and time span', async () => {
  await renderCluster()

  expect(await screen.findByText('3 articles · 2 sources')).toBeTruthy()
  expect(screen.getByText(new RegExp(new Date('2026-09-10T08:00:00Z').toLocaleString().replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))).toBeTruthy()
  expect(api.cluster).toHaveBeenCalledWith('cluster-1', undefined)
})

it('loads more members without replacing earlier ones and opens an article with a return path', async () => {
  vi.mocked(api.cluster)
    .mockResolvedValueOnce(page([{ article_id: 'a1', title: 'First report' }], 'next'))
    .mockResolvedValueOnce(page([{ article_id: 'a2', title: 'Second report' }], null))
  const { router } = await renderCluster()

  await screen.findByText('First report')
  await fireEvent.click(screen.getByRole('button', { name: 'Load more' }))

  expect(await screen.findByText('Second report')).toBeTruthy()
  expect(screen.getByText('First report')).toBeTruthy()
  expect(api.cluster).toHaveBeenLastCalledWith('cluster-1', 'next')

  await fireEvent.click(screen.getByRole('button', { name: /First report/ }))
  await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/articles'))
  expect(router.currentRoute.value.query).toEqual({ article: 'a1', from: '/clusters/cluster-1' })
})

it('links to search within this story, preserving the originating investigation', async () => {
  const { router } = await renderCluster('/clusters/cluster-1?from=%2Fsearch%3Fq%3Dgrid%26country%3DUS')

  await screen.findByText('First report')
  await fireEvent.click(screen.getByRole('link', { name: 'Search within this story' }))

  await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/search'))
  expect(router.currentRoute.value.query).toEqual({ q: 'grid', country: ['US'], story_cluster_id: ['cluster-1'] })
})

it('shows an error state when the cluster cannot be loaded', async () => {
  vi.mocked(api.cluster).mockRejectedValue(new Error('boom'))
  await renderCluster()

  expect(await screen.findByText('Could not load this story.')).toBeTruthy()
})
