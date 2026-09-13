import { fireEvent, render, screen } from '@testing-library/vue'
import { VueQueryPlugin } from '@tanstack/vue-query'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, expect, it, vi } from 'vitest'

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

async function renderView(path = '/') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: ArticlesView }],
  })
  await router.push(path)
  await router.isReady()
  render(ArticlesView, { global: { plugins: [VueQueryPlugin, router] } })
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
