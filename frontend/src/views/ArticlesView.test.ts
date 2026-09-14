import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import ArticlesView from './ArticlesView.vue'
import { api } from '../api'

vi.mock('../api', () => ({
  api: {
    feeds: vi.fn(),
    articles: vi.fn(),
    article: vi.fn(),
    processArticle: vi.fn(),
  },
}))

const article = (id: string, title: string) => ({
  id,
  title,
  original_url: `https://example.com/${id}`,
  normalized_url: `https://example.com/${id}`,
  published_at: null,
  first_discovered_at: '2026-09-13T12:00:00Z',
  provenance: [],
})

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.feeds).mockResolvedValue({ items: [], next_cursor: null })
  vi.mocked(api.articles)
    .mockResolvedValueOnce({ items: [article('one', 'First article')], next_cursor: 'next' })
    .mockResolvedValueOnce({ items: [article('two', 'Second article')], next_cursor: null })
})

afterEach(cleanup)

async function renderView(path = '/') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: ArticlesView }],
  })
  await router.push(path)
  await router.isReady()
  render(ArticlesView, {
    global: { plugins: [[VueQueryPlugin, { queryClient: new QueryClient() }], router] },
  })
  return router
}

it('loads the next cursor page without replacing earlier articles', async () => {
  await renderView()

  expect(await screen.findByText('First article')).toBeTruthy()
  await fireEvent.click(screen.getByRole('button', { name: 'Load more articles' }))

  expect(await screen.findByText('Second article')).toBeTruthy()
  expect(screen.getByText('First article')).toBeTruthy()
  expect(api.articles).toHaveBeenLastCalledWith(undefined, 'next')
})

it('disables processing actions while a request is pending', async () => {
  vi.mocked(api.articles).mockReset().mockResolvedValue({ items: [article('one', 'First article')], next_cursor: null })
  vi.mocked(api.article).mockResolvedValue({
    ...article('one', 'First article'), content: null, processing: [],
  })
  vi.mocked(api.processArticle).mockReturnValue(new Promise(() => {}))
  await renderView('/?article=one')

  const button = await screen.findByRole('button', { name: 'Fetch text' })
  await fireEvent.click(button)

  expect(button.hasAttribute('disabled')).toBe(true)
  expect(screen.getByText('Scheduling processing…')).toBeTruthy()
})

it('loads sources beyond the first page into the filter', async () => {
  vi.mocked(api.feeds)
    .mockReset()
    .mockResolvedValueOnce({ items: [{ id: 'feed-one', name: 'First source' } as never], next_cursor: 'feed-next' })
    .mockResolvedValueOnce({ items: [{ id: 'feed-two', name: 'Second source' } as never], next_cursor: null })
  await renderView()

  await fireEvent.click(await screen.findByRole('button', { name: 'Load more sources' }))

  expect(await screen.findByRole('option', { name: 'Second source' })).toBeTruthy()
  expect(api.feeds).toHaveBeenLastCalledWith('feed-next')
})

it('starts article pagination over when the source filter changes', async () => {
  vi.mocked(api.feeds).mockResolvedValue({
    items: [{ id: 'feed-one', name: 'First source' } as never], next_cursor: null,
  })
  vi.mocked(api.articles).mockReset().mockImplementation(async (feedId, cursor) => {
    if (feedId === 'feed-one') return { items: [article('filtered', 'Filtered article')], next_cursor: null }
    if (cursor === 'next') return { items: [article('two', 'Second article')], next_cursor: null }
    return { items: [article('one', 'First article')], next_cursor: 'next' }
  })
  await renderView()
  await fireEvent.click(await screen.findByRole('button', { name: 'Load more articles' }))
  await fireEvent.update(screen.getByRole('combobox', { name: 'Source' }), 'feed-one')

  await vi.waitFor(() => expect(api.articles).toHaveBeenLastCalledWith('feed-one', undefined))
})

it('shows scheduling success and clears it when another article is selected', async () => {
  vi.mocked(api.articles).mockReset().mockResolvedValue({
    items: [article('one', 'First article'), article('two', 'Second article')], next_cursor: null,
  })
  vi.mocked(api.article).mockImplementation(async (id) => ({
    ...article(id, `${id} detail`), content: null, processing: [],
  }))
  vi.mocked(api.processArticle).mockResolvedValue({ job_id: 'job', status: 'queued', reused: false })
  await renderView('/?article=one')

  await fireEvent.click(await screen.findByRole('button', { name: 'Fetch text' }))
  expect(await screen.findByText('Processing scheduled.')).toBeTruthy()
  await fireEvent.click(screen.getByRole('button', { name: /Second article/ }))

  await vi.waitFor(() => expect(screen.queryByText('Processing scheduled.')).toBeNull())
})
