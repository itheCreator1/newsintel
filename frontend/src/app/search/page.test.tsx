import { cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../../lib/api'
import { navigationHarness, resetNavigationHarness } from '../../test/navigation-harness'
import { renderWithQuery } from '../../test/render'
import SearchPageRoute from './page'

vi.mock('../../lib/api', async importOriginal => ({ ...(await importOriginal<typeof import('../../lib/api')>()), api: { search: vi.fn(), timeline: vi.fn(), createSavedSearch: vi.fn(), createMonitor: vi.fn(), searchSources: vi.fn(), nlpEntities: vi.fn(), nlpKeywords: vi.fn(), article: vi.fn(), processArticle: vi.fn() } }))
// ECharts needs a canvas, so the chart is replaced by a control that emits the brushed bucket span.
vi.mock('../../components/TimelineChart', () => ({
  TimelineChart: (props: { buckets?: unknown[]; interval?: string; onSelect: (range: { start: string; end: string }) => void }) =>
    <button type="button" onClick={() => props.onSelect({ start: '2026-01-05T00:00:00Z', end: '2026-01-19T00:00:00Z' })}>{`Brush ${props.buckets?.length} ${props.interval} buckets`}</button>,
}))

const result = { article_id: 'a1', title: 'Safe <script> title', effective_date: '2026-09-14T12:00:00Z', distinct_source_count: 2, sources: ['Wire'], source_refs: [{ id: 's1', name: 'Wire', country: 'US' }], story_country: 'DE', summary: 'marked text', highlights: [{ text: '<img>', marked: true }, { text: ' safe', marked: false }] }

beforeEach(() => {
  vi.clearAllMocks()
  resetNavigationHarness({ pathname: '/search/' })
  vi.mocked(api.search).mockResolvedValue({ items: [result], next_cursor: 'next' })
  vi.mocked(api.timeline).mockResolvedValue({ interval: 'week', total: 5, buckets: [{ start: '2026-01-05T00:00:00Z', count: 3 }, { start: '2026-01-12T00:00:00Z', count: 2 }] })
  vi.mocked(api.searchSources).mockResolvedValue({ items: [{ id: 's1', name: 'Wire', source_country: 'US', retired: true }], next_cursor: null })
  vi.mocked(api.nlpEntities).mockResolvedValue({ items: [{ id: 'entity-one', kind: 'ORG', normalized_text: 'acme', text: 'Acme' }], next_cursor: null })
  vi.mocked(api.nlpKeywords).mockResolvedValue({ items: [{ id: 'keyword-one', kind: 'keyword', normalized_text: 'climate policy', text: 'climate policy' }], next_cursor: null })
})
afterEach(cleanup)

function renderSearch(search = '') {
  resetNavigationHarness({ pathname: '/search/', search })
  return renderWithQuery(() => <SearchPageRoute />)
}

it('restores criteria from the URL, renders safe highlights, and loads a cursor page', async () => {
  const { container } = renderSearch('q=climate&sort=newest&country=US')
  expect(await screen.findByDisplayValue('climate')).toBeTruthy()
  expect(await screen.findByText('<img>')).toBeTruthy()
  expect(container.querySelector('mark')?.textContent).toBe('<img>')
  expect(container.querySelector('img')).toBeNull()
  await fireEvent.click(screen.getByRole('button', { name: 'Load more' }))
  await vi.waitFor(() => expect(api.search).toHaveBeenCalledWith(expect.anything(), 'next'))
})

it('commits filters to the URL and resets pagination', async () => {
  const { rerenderSame } = renderSearch('q=old')
  await screen.findByText('Safe <script> title')
  fireEvent.change(screen.getByLabelText('Query'), { target: { value: 'new phrase' } })
  await fireEvent.submit(screen.getByRole('search'))
  rerenderSame()

  expect(navigationHarness.searchParams.get('q')).toBe('new phrase')
  await vi.waitFor(() => expect(api.search).toHaveBeenLastCalledWith(expect.objectContaining({ q: 'new phrase' }), undefined))
})

it('restores annotation criteria and explains annotation syntax', async () => {
  const { rerenderSame } = renderSearch('language=en&story_country=DE&entity_id=entity-one')
  expect(await screen.findByDisplayValue('en')).toBeTruthy()
  expect(screen.getByDisplayValue('DE')).toBeTruthy()
  expect(await screen.findByRole('option', { name: 'Acme (ORG)' })).toBeTruthy()
  fireEvent.change(screen.getByLabelText('Keyword'), { target: { value: 'keyword-one' } })
  await fireEvent.submit(screen.getByRole('search'))
  rerenderSame()

  expect(navigationHarness.searchParams.getAll('keyword_id')).toEqual(['keyword-one'])
  await vi.waitFor(() => expect(api.search).toHaveBeenLastCalledWith(expect.objectContaining({ language: ['en'], story_country: ['DE'], entity_id: ['entity-one'], keyword_id: ['keyword-one'] }), undefined))
  expect(screen.getByText(/entity:, keyword:, language:, story_country:/)).toBeTruthy()
})

it('preserves repeated picker selections as OR values in the URL', async () => {
  vi.mocked(api.nlpEntities).mockResolvedValue({ items: [
    { id: 'entity-one', kind: 'ORG', normalized_text: 'acme', text: 'Acme' },
    { id: 'entity-two', kind: 'PERSON', normalized_text: 'jane doe', text: 'Jane Doe' },
  ], next_cursor: null })
  const { rerenderSame } = renderSearch('entity_id=entity-one&entity_id=entity-two')

  const picker = await screen.findByLabelText('Entity') as HTMLSelectElement
  await vi.waitFor(() => expect([...picker.selectedOptions].map(option => option.value)).toEqual(['entity-one', 'entity-two']))
  await fireEvent.submit(screen.getByRole('search'))
  rerenderSame()

  expect(navigationHarness.searchParams.getAll('entity_id')).toEqual(['entity-one', 'entity-two'])
  await vi.waitFor(() => expect(api.search).toHaveBeenLastCalledWith(expect.objectContaining({ entity_id: ['entity-one', 'entity-two'] }), undefined))
})

it('sends the canonical array criteria and keeps the source country bookmark key', async () => {
  renderSearch('country=us&country=GR&processing_status=failed')
  await screen.findByText('Safe <script> title')

  expect(api.search).toHaveBeenLastCalledWith({ source_country: ['US', 'GR'], processing_status: ['failed'], sort: 'relevance' }, undefined)
  expect(api.timeline).toHaveBeenLastCalledWith({ source_country: ['US', 'GR'], processing_status: ['failed'], interval: 'auto' })
  await fireEvent.submit(screen.getByRole('search'))
  expect(navigationHarness.searchParams.getAll('country')).toEqual(['US', 'GR'])
  expect(navigationHarness.searchParams.getAll('processing_status')).toEqual(['failed'])
})

it('cross-filters from a result source or story country and keeps the investigation, and supports back navigation', async () => {
  const { rerenderSame } = renderSearch('q=grid&after=2026-01-01')

  await fireEvent.click(await screen.findByRole('button', { name: 'Filter by source Wire' }))
  expect(navigationHarness.searchParams.getAll('source_id')).toEqual(['s1'])
  rerenderSame()
  await fireEvent.click(await screen.findByRole('button', { name: 'Filter by story country DE' }))
  expect(navigationHarness.searchParams.getAll('story_country')).toEqual(['DE'])
  rerenderSame()

  navigationHarness.back()
  rerenderSame()
  expect(navigationHarness.searchParams.get('story_country')).toBeNull()
  await vi.waitFor(() => expect(api.search).toHaveBeenLastCalledWith({ q: 'grid', after: '2026-01-01', source_id: ['s1'], sort: 'relevance' }, undefined))
})

it('links a multi-source result to its cluster and hides the link for single-source results', async () => {
  vi.mocked(api.search).mockResolvedValueOnce({ items: [
    { ...result, article_id: 'a1', story_cluster: { id: 'cluster-1', source_count: 3 } },
    { ...result, article_id: 'a2', distinct_source_count: 1, story_cluster: { id: 'cluster-2', source_count: 1 } },
  ], next_cursor: null })
  renderSearch('q=grid')

  const link = await screen.findByRole('link', { name: 'Also reported by 1 other source' })
  expect(screen.queryAllByRole('link', { name: /Also reported by/ })).toHaveLength(1)
  expect(link).toHaveAttribute('href', '/clusters/?id=cluster-1&from=%2Fsearch%2F%3Fq%3Dgrid')
})

it('opens an article with a return path to the investigation', async () => {
  renderSearch('q=grid')

  await fireEvent.click(await screen.findByRole('button', { name: /Safe <script> title/ }))

  expect(navigationHarness.pathname).toBe('/articles/')
  expect(navigationHarness.searchParams.get('article')).toBe('a1')
  expect(navigationHarness.searchParams.get('from')).toBe('/search/?q=grid')
})

it('applies a brushed timeline span as the date range and resets pagination', async () => {
  const { rerenderSame } = renderSearch('q=grid&interval=week')

  expect(await screen.findByText('5 matching articles over time')).toBeTruthy()
  await fireEvent.click(await screen.findByRole('button', { name: 'Brush 2 week buckets' }))
  rerenderSame()

  expect(navigationHarness.searchParams.get('after')).toBe('2026-01-05')
  expect(navigationHarness.searchParams.get('before')).toBe('2026-01-19')
  await vi.waitFor(() => expect(api.search).toHaveBeenLastCalledWith(expect.objectContaining({ after: '2026-01-05', before: '2026-01-19' }), undefined))
  expect(api.timeline).toHaveBeenLastCalledWith({ q: 'grid', after: '2026-01-05', before: '2026-01-19', interval: 'week' })
  expect(screen.getByRole('button', { name: 'Clear date range' })).toBeTruthy()
})

it('writes a manual interval to the URL and explains when it is too fine', async () => {
  vi.mocked(api.timeline).mockImplementation(async filters => {
    if (filters.interval === 'hour') throw new ApiError('Hour buckets for this range would exceed 200', 422, { code: 'timeline_too_fine', message: 'Hour buckets for this range would exceed 200' })
    return { interval: 'day', total: 0, buckets: [] }
  })
  const { rerenderSame } = renderSearch('q=grid')

  expect(await screen.findByText('No matching articles to chart.')).toBeTruthy()
  fireEvent.change(screen.getByLabelText('Timeline interval'), { target: { value: 'hour' } })
  rerenderSame()

  expect(navigationHarness.searchParams.get('interval')).toBe('hour')
  expect(await screen.findByText('Hour buckets for this range would exceed 200. Choose a larger interval.')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Use automatic interval' }))
  rerenderSame()
  expect(navigationHarness.searchParams.get('interval')).toBeNull()
})

it('saves the complete investigation state by name', async () => {
  vi.mocked(api.createSavedSearch).mockResolvedValueOnce({} as never).mockRejectedValueOnce(new ApiError('A saved search with this name already exists', 409))
  renderSearch('q=grid&country=GR&entity_id=entity-one&sort=newest&interval=month&before=2026-02-01')
  await screen.findByText('Safe <script> title')

  fireEvent.change(screen.getByLabelText('Saved search name'), { target: { value: 'Greek grid' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Save search' }))

  await vi.waitFor(() => expect(api.createSavedSearch).toHaveBeenCalledWith('Greek grid', {
    q: 'grid', source_id: [], source_country: ['GR'], after: null, before: '2026-02-01', content_available: null, processing_status: [], language: [],
    entity_id: ['entity-one'], entity_type: [], keyword_id: [], story_country: [], mentioned_country: [], story_cluster_id: [], sort: 'newest', interval: 'month',
  }))
  expect(await screen.findByText('Saved “Greek grid”.')).toBeTruthy()
  await fireEvent.click(screen.getByRole('button', { name: 'Save search' }))
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'A saved search with this name already exists')
})

it('keeps a brushed edge bucket inside the active date range', async () => {
  const { rerenderSame } = renderSearch('q=grid&after=2026-01-07&interval=week')

  await fireEvent.click(await screen.findByRole('button', { name: 'Brush 2 week buckets' }))
  rerenderSame()

  expect(navigationHarness.searchParams.get('before')).toBe('2026-01-19')
  expect(navigationHarness.searchParams.get('after')).toBe('2026-01-07')
})

it('never widens an active end date when brushing the last bucket', async () => {
  const { rerenderSame } = renderSearch('q=grid&before=2026-01-15&interval=week')

  await fireEvent.click(await screen.findByRole('button', { name: 'Brush 2 week buckets' }))
  rerenderSame()

  expect(navigationHarness.searchParams.get('after')).toBe('2026-01-05')
  expect(navigationHarness.searchParams.get('before')).toBe('2026-01-15')
})

it('watches the complete investigation state by name and reports a duplicate', async () => {
  vi.mocked(api.createMonitor).mockResolvedValueOnce({} as never).mockRejectedValueOnce(new ApiError('A monitor with this name already exists', 409))
  renderSearch('q=grid&country=GR&sort=newest')
  await screen.findByText('Safe <script> title')

  fireEvent.change(screen.getByLabelText('Monitor name'), { target: { value: 'Greek grid' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Watch search' }))

  await vi.waitFor(() => expect(api.createMonitor).toHaveBeenCalledWith('Greek grid', expect.objectContaining({ q: 'grid', source_country: ['GR'], sort: 'newest' })))
  expect(await screen.findByText('Watching “Greek grid”.')).toBeTruthy()
  expect(screen.getByRole('link', { name: 'Open watchlist' })).toHaveAttribute('href', '/monitors/')
  await fireEvent.click(screen.getByRole('button', { name: 'Watch search' }))
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'A monitor with this name already exists')
})
