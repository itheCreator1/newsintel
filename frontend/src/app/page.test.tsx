import { focusManager } from '@tanstack/react-query'
import { act, cleanup, fireEvent, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../lib/api'
import { navigationHarness, resetNavigationHarness } from '../test/navigation-harness'
import { renderWithQuery } from '../test/render'
import { monitor } from '../test/monitors'
import TodayPage from './page'

vi.mock('../lib/api', async importOriginal => ({ ...(await importOriginal<typeof import('../lib/api')>()), api: { ingestionTimeline: vi.fn(), topEntities: vi.fn(), topCountries: vi.fn(), feeds: vi.fn(), articles: vi.fn(), indexingStatus: vi.fn(), nlpStatus: vi.fn(), monitors: vi.fn(), events: vi.fn(), risingEntities: vi.fn(), opsHealth: vi.fn(), processes: vi.fn() } }))
// ECharts needs a canvas, so each chart is replaced by a control that emits its first item on click.
vi.mock('../components/BarChart', () => ({
  BarChart: (props: { items: { id: string }[]; ariaLabel: string; onSelect: (item: { id: string }) => void }) =>
    <button type="button" onClick={() => props.onSelect(props.items[0])}>{props.ariaLabel}</button>,
}))

beforeEach(() => {
  vi.clearAllMocks()
  resetNavigationHarness({ pathname: '/' })
  vi.mocked(api.monitors).mockResolvedValue({ items: [
    monitor({ id: 'm1', name: 'Harbor watch', unseen_article_count: 3, unseen_cluster_count: 1 }),
    monitor({ id: 'm2', name: 'Quiet one' }),
    monitor({ id: 'm3', name: 'Paused one', enabled: false, unseen_article_count: 9 }),
  ], next_cursor: null })
  vi.mocked(api.events).mockResolvedValue({ items: [event('ev-1', 'Port strike spreads', 12, 5), event('ev-2', null, 4, 2)], next_cursor: null })
  vi.mocked(api.risingEntities).mockResolvedValue({ entities: [{ entity_id: 'e-surge', display_text: 'Harbour Authority', entity_type: 'ORG', today: 12, daily_average: 2 }] })
  vi.mocked(api.opsHealth).mockResolvedValue(health())
  vi.mocked(api.processes).mockResolvedValue({ generated_at: NOW, window_hours: 24, window_start: NOW, processes: [card('feeds', 'Feed fetching', 'ok')] })
  vi.mocked(api.ingestionTimeline).mockResolvedValue({ buckets: [
    { date: '2026-09-01', count: 3, is_spike: false },
    { date: '2026-09-02', count: 9, is_spike: true },
  ] })
  vi.mocked(api.topEntities).mockResolvedValue({ entities: [{ entity_id: 'entity-one', display_text: 'Acme', entity_type: 'ORG', count: 5 }] })
  vi.mocked(api.topCountries).mockResolvedValue({ countries: [{ country_code: 'GR', count: 4 }] })
  // A set-up archive: the getting-started checklist and the entity notice stay hidden.
  vi.mocked(api.feeds).mockResolvedValue({ items: [{} as never], next_cursor: null })
  vi.mocked(api.articles).mockResolvedValue({ items: [{} as never], next_cursor: null })
  vi.mocked(api.indexingStatus).mockResolvedValue({ queued: 0, running: 0, retrying: 0, failed: 0, active_rebuild: null, index_ready: true })
  vi.mocked(api.nlpStatus).mockResolvedValue({ queued: 0, running: 0, retrying: 0, failed: 0, capabilities: [{ name: 'entities', state: 'available', detail: null, version: '3.8' }], reprocessing: [] })
})
afterEach(cleanup)

const NOW = '2026-10-12T09:00:00Z'
const event = (id: string, headline: string | null, articles: number, sources: number) => ({
  id, algorithm_version: 'v1', status: 'active', started_at: '2026-10-11T10:00:00Z', ended_at: '2026-10-12T08:00:00Z', primary_country: 'GR',
  cluster_count: 2, article_count: articles, source_count: sources, headline, headline_article_id: null, entities: [],
})
const health = (states: Record<string, 'ok' | 'degraded' | 'down' | 'unknown'> = {}) => ({
  generated_at: NOW, queues: [],
  probes: ['postgres', 'redis', 'elasticsearch', 'scheduler', 'workers'].map(name => ({ name, state: states[name] ?? 'ok', latency_ms: 1, detail: null })),
}) as never
const card = (key: string, label: string, state: string) => ({ key, group: 'per_item', label, description: '', state }) as never

it('opens a date-filtered search when the ingestion timeline is clicked', async () => {
  renderWithQuery(() => <TodayPage />)

  await fireEvent.click(await screen.findByRole('button', { name: /Articles ingested per day/ }))

  expect(navigationHarness.pathname).toBe('/search/')
  expect(navigationHarness.searchParams.get('after')).toBe('2026-09-01')
  expect(navigationHarness.searchParams.get('before')).toBe('2026-09-02')
})

it('opens an entity-filtered search when a top entity bar is clicked', async () => {
  renderWithQuery(() => <TodayPage />)

  await fireEvent.click(await screen.findByRole('button', { name: /Top named entities/ }))

  expect(navigationHarness.pathname).toBe('/search/')
  expect(navigationHarness.searchParams.getAll('entity_id')).toEqual(['entity-one'])
})

it('opens a country-filtered search when a top country bar is clicked', async () => {
  renderWithQuery(() => <TodayPage />)

  await fireEvent.click(await screen.findByRole('button', { name: /Top primary story countries/ }))

  expect(navigationHarness.pathname).toBe('/search/')
  expect(navigationHarness.searchParams.getAll('story_country')).toEqual(['GR'])
})

it('refetches top entities when the entity type filter changes', async () => {
  renderWithQuery(() => <TodayPage />)
  await screen.findByRole('button', { name: /Top named entities/ })

  fireEvent.change(screen.getByLabelText('Entity type'), { target: { value: 'PERSON' } })

  await vi.waitFor(() => expect(api.topEntities).toHaveBeenLastCalledWith('PERSON'))
})

it('offers the Topic type the user can give an entity', async () => {
  renderWithQuery(() => <TodayPage />)
  await screen.findByRole('button', { name: /Top named entities/ })

  fireEvent.change(screen.getByLabelText('Entity type'), { target: { value: 'TOPIC' } })

  await vi.waitFor(() => expect(api.topEntities).toHaveBeenLastCalledWith('TOPIC'))
})

it('shows an empty-state message instead of a chart when a panel has no data', async () => {
  vi.mocked(api.ingestionTimeline).mockResolvedValue({ buckets: [] })
  vi.mocked(api.topEntities).mockResolvedValue({ entities: [] })
  vi.mocked(api.topCountries).mockResolvedValue({ countries: [] })
  renderWithQuery(() => <TodayPage />)

  expect(await screen.findByText('No articles ingested yet.')).toBeTruthy()
  expect(await screen.findByText('No entities found yet.')).toBeTruthy()
  expect(await screen.findByText('No countries found yet.')).toBeTruthy()
  expect(screen.queryByRole('button', { name: /Articles ingested per day/ })).toBeNull()
})

it('announces loading, then points an empty archive at its sources', async () => {
  vi.mocked(api.ingestionTimeline).mockResolvedValue({ buckets: [] })
  renderWithQuery(() => <TodayPage />)

  expect(screen.getAllByRole('status').map(status => status.textContent)).toContain('Loading ingestion timeline…')
  expect(await screen.findByRole('link', { name: 'Manage sources' })).toHaveAttribute('href', '/sources/')
})

it('retries a failed panel and shows its data once the retry succeeds', async () => {
  vi.mocked(api.topCountries).mockRejectedValueOnce(new Error('offline'))
  renderWithQuery(() => <TodayPage />)

  expect(await screen.findByText('Could not load top countries.')).toBeTruthy()
  expect(screen.queryByText('No countries found yet.')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  expect(await screen.findByRole('button', { name: /Top primary story countries/ })).toBeTruthy()
  expect(screen.queryByText('Could not load top countries.')).toBeNull()
})

it('keeps a loaded chart when a background refetch fails', async () => {
  renderWithQuery(() => <TodayPage />)
  await screen.findByRole('button', { name: /Top primary story countries/ })
  vi.mocked(api.topCountries).mockRejectedValueOnce(new Error('offline'))
  act(() => { focusManager.setFocused(false); focusManager.setFocused(true) })

  expect(await screen.findByText('Could not load top countries.')).toBeTruthy()
  expect(screen.getByRole('button', { name: /Top primary story countries/ })).toBeTruthy()
  focusManager.setFocused(undefined)
})

it('says search needs an upgrade, without a retry, when analytics need a newer index', async () => {
  vi.mocked(api.topEntities).mockRejectedValue(new ApiError('Rebuild search', 503, { code: 'search_upgrade_required', message: 'Rebuild search' }))
  renderWithQuery(() => <TodayPage />)

  expect(await screen.findByText('Search upgrade required. Rebuild the search index to see analytics.')).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull()
})

it('offers a retry when analytics are temporarily unavailable', async () => {
  vi.mocked(api.topCountries).mockRejectedValueOnce(new ApiError('Analytics are unavailable', 503))
  renderWithQuery(() => <TodayPage />)

  expect(await screen.findByText('Analytics are temporarily unavailable.')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  expect(await screen.findByRole('button', { name: /Top primary story countries/ })).toBeTruthy()
})

it('is called Today and leads with what is new', async () => {
  renderWithQuery(() => <TodayPage />)

  expect(screen.getByRole('heading', { level: 2, name: 'Today' })).toBeTruthy()
  // Only watched investigations with news; a paused one keeps old counts and does not call for attention.
  const watched = await screen.findByRole('region', { name: 'Watched investigations' })
  expect(await within(watched).findByRole('link', { name: /Harbor watch/ })).toHaveAttribute('href', '/monitors/?id=m1')
  expect(within(watched).getByText('3 new articles · 1 new story')).toBeTruthy()
  expect(within(watched).queryByText('Quiet one')).toBeNull()
  expect(within(watched).queryByText('Paused one')).toBeNull()

  const events = screen.getByRole('region', { name: 'Events of the last day' })
  expect(await within(events).findByRole('link', { name: 'Port strike spreads' })).toHaveAttribute('href', '/events/detail/?id=ev-1')
  expect(within(events).getByText('12 articles · 5 sources')).toBeTruthy()
  expect(within(events).getByRole('link', { name: 'Untitled event' })).toBeTruthy()
  const since = new Date(vi.mocked(api.events).mock.lastCall![0].from!).getTime()
  expect(Math.abs(since - (Date.now() - 24 * 3600 * 1000))).toBeLessThan(60_000)
  expect(vi.mocked(api.events).mock.lastCall![0].sort).toBe('biggest')

  const rising = screen.getByRole('region', { name: 'Rising names' })
  expect(await within(rising).findByRole('link', { name: 'Harbour Authority' })).toHaveAttribute('href', '/entities/?id=e-surge')
  expect(within(rising).getByText('12 today · 2 a day before')).toBeTruthy()
})

it('says when nothing is new instead of leaving the panels empty', async () => {
  vi.mocked(api.monitors).mockResolvedValue({ items: [monitor({ id: 'm2', name: 'Quiet one' })], next_cursor: null })
  vi.mocked(api.events).mockResolvedValue({ items: [], next_cursor: null })
  vi.mocked(api.risingEntities).mockResolvedValue({ entities: [] })
  renderWithQuery(() => <TodayPage />)

  expect(await screen.findByText('Nothing new in your watched investigations.')).toBeTruthy()
  expect(screen.getByRole('link', { name: 'Open Investigations' })).toHaveAttribute('href', '/investigations/')
  expect(await screen.findByText('No events in the last day.')).toBeTruthy()
  expect(await screen.findByText('No name stands out today.')).toBeTruthy()
})

it('shows one health line only when a service or process needs attention', async () => {
  const first = renderWithQuery(() => <TodayPage />)
  await screen.findByRole('region', { name: 'Watched investigations' })
  await vi.waitFor(() => expect(api.processes).toHaveBeenCalled())
  expect(screen.queryByRole('status', { name: 'System health' })).toBeNull()
  first.unmount()

  vi.mocked(api.opsHealth).mockResolvedValue(health({ elasticsearch: 'down' }))
  vi.mocked(api.processes).mockResolvedValue({ generated_at: NOW, window_hours: 24, window_start: NOW, processes: [card('feeds', 'Feed fetching', 'failing'), card('nlp', 'NLP', 'ok')] })
  renderWithQuery(() => <TodayPage />)

  const line = await screen.findByRole('status', { name: 'System health' })
  await vi.waitFor(() => expect(line).toHaveTextContent('Elasticsearch is down. Feed fetching is failing.'))
  expect(within(line).getByRole('link', { name: 'Open Processes' })).toHaveAttribute('href', '/processes/')
})
