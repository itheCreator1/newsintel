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

it('loads the next cursor page without replacing earlier articles', async () => {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: ArticlesView }],
  })
  await router.push('/')
  await router.isReady()
  render(ArticlesView, { global: { plugins: [VueQueryPlugin, router] } })

  expect(await screen.findByText('First article')).toBeTruthy()
  await fireEvent.click(screen.getByRole('button', { name: 'Load more articles' }))

  expect(await screen.findByText('Second article')).toBeTruthy()
  expect(screen.getByText('First article')).toBeTruthy()
  expect(api.articles).toHaveBeenLastCalledWith(undefined, 'next')
})
