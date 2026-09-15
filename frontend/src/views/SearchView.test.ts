import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../api'
import SearchView from './SearchView.vue'

vi.mock('../api', async importOriginal => ({ ...(await importOriginal<typeof import('../api')>()), api: { search: vi.fn(), timeline: vi.fn(), createSavedSearch: vi.fn(), searchSources: vi.fn(), nlpEntities: vi.fn(), nlpKeywords: vi.fn(), article: vi.fn(), processArticle: vi.fn() } }))
// ECharts needs a canvas, so the chart is replaced by a control that emits the brushed bucket span.
vi.mock('../components/TimelineChart.vue', async () => { const { defineComponent, h } = await import('vue'); return { default: defineComponent({ props: { buckets: Array, interval: String }, emits: ['select'], setup: (props, { emit }) => () => h('button', { type: 'button', onClick: () => emit('select', { start: '2026-01-05T00:00:00Z', end: '2026-01-19T00:00:00Z' }) }, `Brush ${props.buckets?.length} ${props.interval} buckets`) }) } })
const result = { article_id: 'a1', title: 'Safe <script> title', effective_date: '2026-09-14T12:00:00Z', distinct_source_count: 2, sources: ['Wire'], source_refs: [{ id: 's1', name: 'Wire', country: 'US' }], story_country: 'DE', summary: 'marked text', highlights: [{ text: '<img>', marked: true }, { text: ' safe', marked: false }] }

beforeEach(() => { vi.clearAllMocks(); vi.mocked(api.search).mockResolvedValue({ items: [result], next_cursor: 'next' }); vi.mocked(api.timeline).mockResolvedValue({ interval: 'week', total: 5, buckets: [{ start: '2026-01-05T00:00:00Z', count: 3 }, { start: '2026-01-12T00:00:00Z', count: 2 }] }); vi.mocked(api.searchSources).mockResolvedValue({ items: [{ id: 's1', name: 'Wire', source_country: 'US', retired: true }], next_cursor: null }); vi.mocked(api.nlpEntities).mockResolvedValue({ items: [{ id: 'entity-one', kind: 'ORG', normalized_text: 'acme', text: 'Acme' }], next_cursor: null }); vi.mocked(api.nlpKeywords).mockResolvedValue({ items: [{ id: 'keyword-one', kind: 'keyword', normalized_text: 'climate policy', text: 'climate policy' }], next_cursor: null }) })
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

it('restores annotation criteria and explains annotation syntax', async () => {
  const { router } = await renderSearch('/search?language=en&story_country=DE&entity_id=entity-one')
  expect(await screen.findByDisplayValue('en')).toBeTruthy()
  expect(screen.getByDisplayValue('DE')).toBeTruthy()
  expect(await screen.findByRole('option', { name: 'Acme (ORG)' })).toBeTruthy()
  await fireEvent.update(screen.getByLabelText('Keyword'), 'keyword-one')
  await fireEvent.submit(screen.getByRole('search'))
  await vi.waitFor(() => expect(router.currentRoute.value.query.keyword_id).toEqual(['keyword-one']))
  expect(api.search).toHaveBeenLastCalledWith(expect.objectContaining({ language: ['en'], story_country: ['DE'], entity_id: ['entity-one'], keyword_id: ['keyword-one'] }), undefined)
  expect(screen.getByText(/entity:, keyword:, language:, story_country:/)).toBeTruthy()
})

it('preserves repeated picker selections as OR values in the URL', async () => {
  vi.mocked(api.nlpEntities).mockResolvedValue({ items: [
    { id: 'entity-one', kind: 'ORG', normalized_text: 'acme', text: 'Acme' },
    { id: 'entity-two', kind: 'PERSON', normalized_text: 'jane doe', text: 'Jane Doe' },
  ], next_cursor: null })
  const { router } = await renderSearch('/search?entity_id=entity-one&entity_id=entity-two')

  const picker = await screen.findByLabelText('Entity') as HTMLSelectElement
  await vi.waitFor(() => expect([...picker.selectedOptions].map(option => option.value)).toEqual(['entity-one', 'entity-two']))
  await fireEvent.submit(screen.getByRole('search'))

  await vi.waitFor(() => expect(router.currentRoute.value.query.entity_id).toEqual(['entity-one', 'entity-two']))
  expect(api.search).toHaveBeenLastCalledWith(expect.objectContaining({ entity_id: ['entity-one', 'entity-two'] }), undefined)
})

it('sends the canonical array criteria and keeps the source country bookmark key', async () => {
  const { router } = await renderSearch('/search?country=us&country=GR&processing_status=failed')
  await screen.findByText('Safe <script> title')

  expect(api.search).toHaveBeenLastCalledWith({ source_country: ['US', 'GR'], processing_status: ['failed'], sort: 'relevance' }, undefined)
  expect(api.timeline).toHaveBeenLastCalledWith({ source_country: ['US', 'GR'], processing_status: ['failed'], interval: 'auto' })
  await fireEvent.submit(screen.getByRole('search'))
  await vi.waitFor(() => expect(router.currentRoute.value.query).toEqual({ country: ['US', 'GR'], processing_status: ['failed'] }))
})

it('cross-filters from a result source or story country and keeps the investigation', async () => {
  const { router } = await renderSearch('/search?q=grid&after=2026-01-01')

  await fireEvent.click(await screen.findByRole('button', { name: 'Filter by source Wire' }))
  await vi.waitFor(() => expect(router.currentRoute.value.query).toEqual({ q: 'grid', after: '2026-01-01', source_id: ['s1'] }))
  await fireEvent.click(await screen.findByRole('button', { name: 'Filter by story country DE' }))
  await vi.waitFor(() => expect(router.currentRoute.value.query).toEqual({ q: 'grid', after: '2026-01-01', source_id: ['s1'], story_country: ['DE'] }))
  router.back()
  await vi.waitFor(() => expect(router.currentRoute.value.query.story_country).toBeUndefined())
  await vi.waitFor(() => expect(api.search).toHaveBeenLastCalledWith({ q: 'grid', after: '2026-01-01', source_id: ['s1'], sort: 'relevance' }, undefined))
})

it('opens an article with a return path to the investigation', async () => {
  const { router } = await renderSearch('/search?q=grid')

  await fireEvent.click(await screen.findByRole('button', { name: /Safe <script> title/ }))

  await vi.waitFor(() => expect(router.currentRoute.value.query).toEqual({ article: 'a1', from: '/search?q=grid' }))
})

it('applies a brushed timeline span as the date range and resets pagination', async () => {
  const { router } = await renderSearch('/search?q=grid&interval=week')

  expect(await screen.findByText('5 matching articles over time')).toBeTruthy()
  await fireEvent.click(await screen.findByRole('button', { name: 'Brush 2 week buckets' }))

  await vi.waitFor(() => expect(router.currentRoute.value.query).toEqual({ q: 'grid', interval: 'week', after: '2026-01-05', before: '2026-01-19' }))
  await vi.waitFor(() => expect(api.search).toHaveBeenLastCalledWith(expect.objectContaining({ after: '2026-01-05', before: '2026-01-19' }), undefined))
  expect(api.timeline).toHaveBeenLastCalledWith({ q: 'grid', after: '2026-01-05', before: '2026-01-19', interval: 'week' })
  expect(screen.getByRole('button', { name: 'Clear date range' })).toBeTruthy()
})

it('writes a manual interval to the URL and explains when it is too fine', async () => {
  vi.mocked(api.timeline).mockImplementation(async filters => {
    if (filters.interval === 'hour') throw new ApiError('Hour buckets for this range would exceed 200', 422, { code: 'timeline_too_fine', message: 'Hour buckets for this range would exceed 200' })
    return { interval: 'day', total: 0, buckets: [] }
  })
  const { router } = await renderSearch('/search?q=grid')

  expect(await screen.findByText('No matching articles to chart.')).toBeTruthy()
  await fireEvent.update(screen.getByLabelText('Timeline interval'), 'hour')

  await vi.waitFor(() => expect(router.currentRoute.value.query).toEqual({ q: 'grid', interval: 'hour' }))
  expect(await screen.findByText('Hour buckets for this range would exceed 200. Choose a larger interval.')).toBeTruthy()
  await fireEvent.click(screen.getByRole('button', { name: 'Use automatic interval' }))
  await vi.waitFor(() => expect(router.currentRoute.value.query).toEqual({ q: 'grid' }))
})

it('saves the complete investigation state by name', async () => {
  vi.mocked(api.createSavedSearch).mockResolvedValueOnce({} as never).mockRejectedValueOnce(new ApiError('A saved search with this name already exists', 409))
  await renderSearch('/search?q=grid&country=GR&entity_id=entity-one&sort=newest&interval=month&before=2026-02-01')
  await screen.findByText('Safe <script> title')

  await fireEvent.update(screen.getByLabelText('Saved search name'), 'Greek grid')
  await fireEvent.click(screen.getByRole('button', { name: 'Save search' }))

  await vi.waitFor(() => expect(api.createSavedSearch).toHaveBeenCalledWith('Greek grid', {
    q: 'grid', source_id: [], source_country: ['GR'], after: null, before: '2026-02-01', content_available: null, processing_status: [], language: [],
    entity_id: ['entity-one'], entity_type: [], keyword_id: [], story_country: [], mentioned_country: [], sort: 'newest', interval: 'month',
  }))
  expect(await screen.findByText('Saved “Greek grid”.')).toBeTruthy()
  await fireEvent.click(screen.getByRole('button', { name: 'Save search' }))
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'A saved search with this name already exists')
})
