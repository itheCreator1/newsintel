import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '../api'
import SearchView from './SearchView.vue'

vi.mock('../api', async importOriginal => ({ ...(await importOriginal<typeof import('../api')>()), api: { search: vi.fn(), searchSources: vi.fn(), article: vi.fn(), processArticle: vi.fn() } }))
const result = { article_id: 'a1', title: 'Safe <script> title', effective_date: '2026-09-14T12:00:00Z', distinct_source_count: 2, sources: ['Wire'], summary: 'marked text', highlights: [{ text: '<img>', marked: true }, { text: ' safe', marked: false }] }

beforeEach(() => { vi.clearAllMocks(); vi.mocked(api.search).mockResolvedValue({ items: [result], next_cursor: 'next' }); vi.mocked(api.searchSources).mockResolvedValue({ items: [{ id: 's1', name: 'Wire', source_country: 'US', retired: true }], next_cursor: null }) })
afterEach(cleanup)

async function renderSearch(url = '/search') {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/search', component: SearchView }, { path: '/articles', component: { template: '<div />' } }] })
  await router.push(url); await router.isReady()
  return { router, ...render(SearchView, { global: { plugins: [[VueQueryPlugin, { queryClient: new QueryClient({ defaultOptions: { queries: { retry: false } } }) }], router] } }) }
}

it('restores criteria from the URL, renders safe highlights, and loads a cursor page', async () => {
  const { container } = await renderSearch('/search?q=climate&sort=newest&country=US')
  expect(await screen.findByDisplayValue('climate')).toBeTruthy()
  expect(await screen.findByText('<img>')).toBeTruthy()
  expect(container.querySelector('mark')?.textContent).toBe('<img>')
  expect(container.querySelector('img')).toBeNull()
  await fireEvent.click(screen.getByRole('button', { name: 'Load more' }))
  await vi.waitFor(() => expect(api.search).toHaveBeenCalledWith(expect.anything(), 'next'))
})

it('commits filters to the URL and resets pagination', async () => {
  const { router } = await renderSearch('/search?q=old')
  await screen.findByText('Safe <script> title')
  await fireEvent.update(screen.getByLabelText('Query'), 'new phrase')
  await fireEvent.submit(screen.getByRole('search'))
  await vi.waitFor(() => expect(router.currentRoute.value.query.q).toBe('new phrase'))
  expect(api.search).toHaveBeenLastCalledWith(expect.objectContaining({ q: 'new phrase' }), undefined)
})
