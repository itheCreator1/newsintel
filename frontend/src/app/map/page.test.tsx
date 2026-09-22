import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '../../lib/api'
import type { GeoCountriesResponse } from '../../lib/api-types'
import { navigationHarness, resetNavigationHarness } from '../../test/navigation-harness'
import { renderWithQuery } from '../../test/render'
import MapPage from './page'

vi.mock('../../lib/api', async importOriginal => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: { geoCountries: vi.fn(), geoArticles: vi.fn(), events: vi.fn(), createMonitor: vi.fn() },
}))
vi.mock('../../components/GeoChart', () => ({
  GeoChart: ({ items, selected, ariaLabel, onSelect }: { items: { code: string; label: string; value: number }[]; selected?: string; ariaLabel: string; onSelect(code: string): void }) => (
    <div data-testid="map" aria-label={ariaLabel} data-selected={selected ?? ''}>{items.map(item => <button key={item.code} onClick={() => onSelect(item.code)}>{`map ${item.label} ${item.value}`}</button>)}</div>
  ),
}))

const row = (country_code: string, over = {}) => ({ country_code, articles: 5, stories: 3, sources: null, events: null, ...over })
const response = (over = {}) => ({
  role: 'story', days: 30, window_start: '2026-08-22T00:00:00Z',
  coverage: { unit: 'articles', window_total: 10, located: 4 },
  items: [row('GR'), row('FR', { articles: 2, stories: 1 }), row('XK', { articles: 1, stories: 1 })],
  ...over,
} as unknown as GeoCountriesResponse)
const article = (id: string, title: string) => ({ id, title, original_url: `https://x/${id}`, normalized_url: `https://x/${id}`, published_at: '2026-09-18T10:00:00Z', first_discovered_at: '2026-09-18T11:00:00Z', provenance: [] })
const eventItem = (id: string, headline: string) => ({ id, headline, status: 'active', started_at: '2026-09-18T10:00:00Z', ended_at: '2026-09-19T10:00:00Z', primary_country: 'GR', cluster_count: 2, article_count: 4, source_count: 2, entities: [] })

function open(search = '') {
  resetNavigationHarness({ pathname: '/map/', search })
  return renderWithQuery(() => <MapPage />)
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.geoCountries).mockResolvedValue(response())
  vi.mocked(api.geoArticles).mockResolvedValue({
    items: [article('a1', 'Harbour talks')],
    next_cursor: null,
    skipped_stale: 0,
  })
  vi.mocked(api.events).mockResolvedValue({ items: [eventItem('ev1', 'Harbour fire')], next_cursor: null } as never)
})
afterEach(cleanup)

it('shows the story country map by default with its definition and the base of every count', async () => {
  open()
  expect(await screen.findByText('map Greece 5')).toBeTruthy()
  expect(api.geoCountries).toHaveBeenCalledWith('story', 30)
  expect(screen.getByText(/named alone in its title/)).toBeTruthy()
  expect(screen.getByText('4 of 10 articles in the last 30 days have a story country; 6 have none.')).toBeTruthy()
  const table = screen.getByRole('table')
  expect(within(table).getByText('Greece')).toBeTruthy()
  expect(within(table).getAllByRole('row')).toHaveLength(4) // header and three countries
})

it('reads the role and window from the URL and asks for exactly that', async () => {
  open('role=mentioned&days=90')
  await screen.findByText('map Greece 5')
  expect(api.geoCountries).toHaveBeenCalledWith('mentioned', 90)
  expect((screen.getByLabelText('Location role') as HTMLSelectElement).value).toBe('mentioned')
  expect((screen.getByLabelText('Window') as HTMLSelectElement).value).toBe('90')
})

it('counts events for the event role and says the event country comes from the story country', async () => {
  vi.mocked(api.geoCountries).mockResolvedValue(response({
    role: 'event', coverage: { unit: 'events', window_total: 5, located: 3 },
    items: [row('GR', { articles: null, stories: null, events: 2 })],
  }))
  open('role=event')
  expect(await screen.findByText('map Greece 2')).toBeTruthy()
  expect(screen.getByText(/derived from the story country/)).toBeTruthy()
  expect(screen.getByText('3 of 5 events in the last 30 days have a country; 2 have none.')).toBeTruthy()
  expect(within(screen.getByRole('table')).getByText('Events')).toBeTruthy()
  expect(within(screen.getByRole('table')).queryByText('Stories')).toBeNull()
})

it('shows the feeds count only for the source role', async () => {
  vi.mocked(api.geoCountries).mockResolvedValue(response({ role: 'source', items: [row('GR', { sources: 2 })] }))
  open('role=source')
  await screen.findByText('map Greece 5')
  expect(within(screen.getByRole('table')).getByText('Sources')).toBeTruthy()
})

it('keeps the window and the selected country when the role changes, and the role when the window does', async () => {
  open('days=90&country=GR')
  await screen.findByText('map Greece 5')
  fireEvent.change(screen.getByLabelText('Location role'), { target: { value: 'mentioned' } })
  expect(navigationHarness.replace).toHaveBeenLastCalledWith('/map/?role=mentioned&days=90&selected_country=GR')
  fireEvent.change(screen.getByLabelText('Window'), { target: { value: '7' } })
  expect(navigationHarness.replace).toHaveBeenLastCalledWith('/map/?days=7&selected_country=GR')
})

it('selects a country from the map or the table and clears it again', async () => {
  const view = open()
  fireEvent.click(await screen.findByText('map France 2'))
  expect(navigationHarness.replace).toHaveBeenLastCalledWith('/map/?selected_country=FR')
  fireEvent.click(within(screen.getByRole('table')).getByRole('button', { name: 'Greece' }))
  expect(navigationHarness.replace).toHaveBeenLastCalledWith('/map/?selected_country=GR')

  resetNavigationHarness({ pathname: '/map/', search: 'selected_country=GR' })
  view.rerenderSame()
  fireEvent.click(await screen.findByRole('button', { name: 'Clear selection' }))
  expect(navigationHarness.replace).toHaveBeenLastCalledWith('/map/')
})

it('opens the canonical selected_country as the selection', async () => {
  open('country=GR')
  expect(await screen.findByRole('region', { name: 'Greece' })).toBeTruthy()
})

it('opens a selected country with its counts, its articles and links that refine the investigation', async () => {
  open('country=GR')
  const panel = await screen.findByRole('region', { name: 'Greece' })
  expect(within(panel).getByText('5 articles · 3 stories')).toBeTruthy()
  expect(await within(panel).findByRole('link', { name: 'Harbour talks' })).toBeTruthy()
  expect(api.geoArticles).toHaveBeenCalledWith('story', 'GR', 30, undefined)
  expect(within(panel).getByRole('link', { name: 'Search these articles' }).getAttribute('href')).toBe('/search/?story_country=GR&after=2026-08-22')
  expect(within(panel).getByRole('link', { name: 'Events with this country' }).getAttribute('href')).toBe('/events/?country=GR&from=2026-08-22')
  expect(within(panel).getByRole('link', { name: 'Compare with another country' }).getAttribute('href')).toBe('/compare/?kind=country&a=GR&role=story')
})

it('refines with the field of the role being shown and offers events only for story-based roles', async () => {
  vi.mocked(api.geoCountries).mockResolvedValue(response({ role: 'mentioned' }))
  const { unmount } = open('role=mentioned&country=GR')
  let panel = await screen.findByRole('region', { name: 'Greece' })
  expect(within(panel).getByRole('link', { name: 'Search these articles' }).getAttribute('href')).toBe('/search/?mentioned_country=GR&after=2026-08-22')
  expect(within(panel).getByRole('link', { name: 'Compare with another country' }).getAttribute('href')).toBe('/compare/?kind=country&a=GR&role=mentioned')
  expect(within(panel).queryByRole('link', { name: 'Events with this country' })).toBeNull()
  unmount()

  vi.mocked(api.geoCountries).mockResolvedValue(response({ role: 'source' }))
  open('role=source&country=GR')
  panel = await screen.findByRole('region', { name: 'Greece' })
  expect(within(panel).getByRole('link', { name: 'Search these articles' }).getAttribute('href')).toBe('/search/?country=GR&after=2026-08-22')
})

it('lists events, not articles, for the event role and never offers a search that would mislabel them', async () => {
  vi.mocked(api.geoCountries).mockResolvedValue(response({
    role: 'event', coverage: { unit: 'events', window_total: 5, located: 3 }, items: [row('GR', { articles: null, stories: null, events: 2 })],
  }))
  open('role=event&country=GR')
  const panel = await screen.findByRole('region', { name: 'Greece' })
  expect(await within(panel).findByRole('link', { name: 'Harbour fire' })).toBeTruthy()
  expect(api.events).toHaveBeenCalledWith({ country: 'GR', from: '2026-08-22T00:00:00Z' }, undefined)
  expect(api.geoArticles).not.toHaveBeenCalled()
  expect(within(panel).queryByRole('link', { name: 'Search these articles' })).toBeNull()
  expect(within(panel).queryByRole('link', { name: 'Compare with another country' })).toBeNull()
  expect(within(panel).getByText('2 events')).toBeTruthy()
})

it('loads more articles with the cursor', async () => {
  vi.mocked(api.geoArticles)
    .mockResolvedValueOnce({
      items: [article('a1', 'Harbour talks')],
      next_cursor: 'next',
      skipped_stale: 0,
    })
    .mockResolvedValueOnce({
      items: [article('a2', 'Port strike')],
      next_cursor: null,
      skipped_stale: 0,
    })
  open('country=GR')
  fireEvent.click(await screen.findByRole('button', { name: 'Load more articles' }))
  expect(await screen.findByRole('link', { name: 'Port strike' })).toBeTruthy()
  expect(api.geoArticles).toHaveBeenLastCalledWith('story', 'GR', 30, 'next')
})

it('says so when the selected country has nothing in this role and window', async () => {
  open('country=JP')
  const panel = await screen.findByRole('region', { name: 'Japan' })
  expect(within(panel).getByText('No articles have Japan as a story country in this window.')).toBeTruthy()
  expect(api.geoArticles).not.toHaveBeenCalled()
})

it('lists a country the map cannot draw in the table and says so', async () => {
  open()
  await screen.findByText('map Greece 5')
  vi.mocked(api.geoCountries).mockResolvedValue(response({ items: [row('ZZ', { articles: 1 })] }))
  cleanup()
  open()
  expect(await within(await screen.findByRole('table')).findByText('not drawn on the map')).toBeTruthy()
})

it('reports an empty window, a role with no located items and a failure', async () => {
  vi.mocked(api.geoCountries).mockResolvedValue(response({ coverage: { unit: 'articles', window_total: 0, located: 0 }, items: [] }))
  const first = open()
  expect(await screen.findByText('No articles in the last 30 days.')).toBeTruthy()
  first.unmount()

  vi.mocked(api.geoCountries).mockResolvedValue(response({ coverage: { unit: 'articles', window_total: 8, located: 0 }, items: [] }))
  const second = open()
  expect(await screen.findByText('None of the 8 articles in the last 30 days has a story country.')).toBeTruthy()
  second.unmount()

  vi.mocked(api.geoCountries).mockRejectedValue(new Error('boom'))
  open()
  await waitFor(() => expect(screen.getByText('Could not load the map.')).toBeTruthy())
})

it('ignores a role or window it does not know and a country that is not a code', async () => {
  open('role=everywhere&days=5&country=GRC')
  await screen.findByText('map Greece 5')
  expect(api.geoCountries).toHaveBeenCalledWith('story', 30)
  expect(screen.queryByRole('region', { name: /Greece/ })).toBeNull()
})

it('watches the selected country in the role on screen', async () => {
  vi.mocked(api.createMonitor).mockResolvedValueOnce({} as never)
  vi.mocked(api.geoCountries).mockResolvedValue(response({ role: 'mentioned' }))
  open('role=mentioned&country=GR')

  fireEvent.click(await screen.findByRole('button', { name: 'Watch country' }))
  await vi.waitFor(() => expect(api.createMonitor).toHaveBeenCalledWith('Greece (mentioned country)', expect.objectContaining({ mentioned_country: ['GR'], story_country: [] }), 'country'))
})

it('offers no watch for the event role, which has no monitor field', async () => {
  vi.mocked(api.geoCountries).mockResolvedValue(response({
    role: 'event', coverage: { unit: 'events', window_total: 5, located: 3 },
    items: [row('GR', { articles: null, stories: null, events: 2 })],
  }))
  open('role=event&country=GR')
  expect(await screen.findByRole('heading', { name: 'Greece' })).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Watch country' })).toBeNull()
})
