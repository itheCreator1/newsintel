import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../../../lib/api'
import { resetNavigationHarness } from '../../../test/navigation-harness'
import { renderWithQuery } from '../../../test/render'
import EventDetailPage from './page'

vi.mock('../../../lib/api', async importOriginal => ({
  ...(await importOriginal<typeof import('../../../lib/api')>()),
  api: { event: vi.fn(), eventTimeline: vi.fn(), eventClusters: vi.fn(), eventArticles: vi.fn() },
}))
vi.mock('../../../components/BarChart', () => ({
  BarChart: ({ items, onSelect }: { items: { id: string; label: string }[]; onSelect(item: { id: string }): void }) => (
    <div data-testid="timeline">{items.map(item => <button key={item.id} onClick={() => onSelect(item)}>{item.label}</button>)}</div>
  ),
}))

const detail = (over = {}) => ({
  id: 'ev-1', algorithm_version: 'rule-1', status: 'active', started_at: '2026-09-17T08:00:00Z', ended_at: '2026-09-18T10:00:00Z',
  primary_country: 'GR', cluster_count: 2, article_count: 5, source_count: 3, headline: 'Wildfire reaches Athens', headline_article_id: 'a1',
  created_at: '2026-09-17T09:00:00Z', updated_at: '2026-09-18T11:00:00Z',
  entities: [{ id: 'ent-1', display_name: 'Barack Obama', entity_type: 'PERSON', article_count: 4 }], ...over,
})
const article = (id: string, title: string) => ({ id, title, original_url: `https://x/${id}`, normalized_url: `https://x/${id}`, published_at: '2026-09-18T10:00:00Z', first_discovered_at: '2026-09-18T11:00:00Z', provenance: [] })
const day = (date: string, title: string) => ({ date, article_count: 3, source_count: 2, clusters_started: 1, evidence: [{ article_id: `ev-${date}`, title }] })
const cluster = {
  id: 'c1', article_count: 3, source_count: 2, first_published_at: '2026-09-17T00:00:00Z', last_published_at: '2026-09-18T00:00:00Z',
  representative_article: article('a9', 'Story headline'), score: 0.7234, signals: { time: 0.85, entities: 0.5, title: 0.3, location: 1 }, joined_at: '2026-09-18T12:00:00Z',
}
const member = (id: string, title: string) => ({ ...article(id, title), cluster_id: 'c1' })

beforeEach(() => {
  vi.clearAllMocks()
  resetNavigationHarness({ pathname: '/events/detail/', search: 'id=ev-1&from=%2Fevents%2F%3Fstatus%3Dactive' })
  vi.mocked(api.event).mockResolvedValue(detail())
  vi.mocked(api.eventTimeline).mockResolvedValue({ items: [day('2026-09-17', 'First report'), day('2026-09-18', 'Second report')], next_cursor: null })
  vi.mocked(api.eventClusters).mockResolvedValue({ items: [cluster], next_cursor: null })
  vi.mocked(api.eventArticles).mockResolvedValue({ items: [member('a1', 'Fire spreads')], next_cursor: null })
})
afterEach(cleanup)

it('asks for an event when there is no id', () => {
  resetNavigationHarness({ pathname: '/events/detail/' })
  renderWithQuery(() => <EventDetailPage />)

  expect(screen.getByText(/Choose an event/)).toBeTruthy()
  expect(api.event).not.toHaveBeenCalled()
})

it('shows the header, counts, country, span and entity links', async () => {
  renderWithQuery(() => <EventDetailPage />)

  expect(await screen.findByRole('heading', { name: 'Wildfire reaches Athens' })).toBeTruthy()
  expect(screen.getByText('2 stories · 5 articles · 3 sources')).toBeTruthy()
  expect(screen.getByText(/Country GR/)).toBeTruthy()
  expect(screen.getByRole('link', { name: /Barack Obama/ }).getAttribute('href')).toBe('/entities/?id=ent-1')
  expect(screen.getByRole('link', { name: 'Back to events' }).getAttribute('href')).toBe('/events/?status=active')
})

it('reports a missing event once and does not load its evidence', async () => {
  vi.mocked(api.event).mockRejectedValue(new ApiError('Event not found', 404))
  renderWithQuery(() => <EventDetailPage />)

  expect(await screen.findByText('Event not found.')).toBeTruthy()
  expect(api.eventClusters).not.toHaveBeenCalled()
  expect(api.eventArticles).not.toHaveBeenCalled()
  expect(api.eventTimeline).not.toHaveBeenCalled()
})

it('reports other failures', async () => {
  vi.mocked(api.event).mockRejectedValue(new ApiError('Request failed', 500))
  renderWithQuery(() => <EventDetailPage />)

  expect(await screen.findByText('Could not load this event.')).toBeTruthy()
})

it('shows the first day of the timeline and switches its evidence when another day is picked', async () => {
  renderWithQuery(() => <EventDetailPage />)

  const details = await screen.findByRole('region', { name: 'Day details' })
  expect(within(details).getByText('2026-09-17')).toBeTruthy()
  expect(within(details).getByText('3 articles · 2 sources · 1 story started')).toBeTruthy()
  const evidence = within(details).getByRole('link', { name: 'First report' })
  expect(evidence.getAttribute('href')).toMatch(/^\/articles\/\?article=ev-2026-09-17&from=/)

  fireEvent.click(screen.getByRole('button', { name: '2026-09-18' }))

  expect(within(details).getByRole('link', { name: 'Second report' })).toBeTruthy()
  expect(within(details).queryByRole('link', { name: 'First report' })).toBeNull()
})

it('loads later days of the timeline by date', async () => {
  vi.mocked(api.eventTimeline)
    .mockResolvedValueOnce({ items: [day('2026-09-17', 'First report')], next_cursor: '2026-09-17' })
    .mockResolvedValueOnce({ items: [day('2026-09-19', 'Third report')], next_cursor: null })
  renderWithQuery(() => <EventDetailPage />)

  fireEvent.click(await screen.findByRole('button', { name: 'Load later days' }))

  expect(await screen.findByRole('button', { name: '2026-09-19' })).toBeTruthy()
  expect(api.eventTimeline).toHaveBeenLastCalledWith('ev-1', '2026-09-17')
})

it('lists stories with why they joined and links them to the story page', async () => {
  renderWithQuery(() => <EventDetailPage />)

  const link = await screen.findByRole('link', { name: 'Story headline' })
  expect(link.getAttribute('href')).toMatch(/^\/clusters\/\?id=c1&from=/)
  expect(screen.getByText('3 articles · 2 sources')).toBeTruthy()
  expect(screen.getByText('Joined with score 0.72')).toBeTruthy()
  expect(screen.getByText('time 0.85 · entities 0.50 · title 0.30 · location 1.00')).toBeTruthy()
})

it('lists articles that open the article page and link back to their story', async () => {
  renderWithQuery(() => <EventDetailPage />)

  const link = await screen.findByRole('link', { name: 'Fire spreads' })
  expect(link.getAttribute('href')).toMatch(/^\/articles\/\?article=a1&from=%2Fevents%2Fdetail%2F/)
  expect(screen.getByRole('link', { name: 'Story' }).getAttribute('href')).toMatch(/^\/clusters\/\?id=c1&from=/)
})

it('loads more stories and articles with their cursors', async () => {
  vi.mocked(api.eventClusters)
    .mockResolvedValueOnce({ items: [cluster], next_cursor: 'cc' })
    .mockResolvedValueOnce({ items: [{ ...cluster, id: 'c2', representative_article: article('a8', 'Second story') }], next_cursor: null })
  vi.mocked(api.eventArticles)
    .mockResolvedValueOnce({ items: [member('a1', 'Fire spreads')], next_cursor: 'ac' })
    .mockResolvedValueOnce({ items: [member('a2', 'Fire contained')], next_cursor: null })
  renderWithQuery(() => <EventDetailPage />)

  fireEvent.click(await screen.findByRole('button', { name: 'Load more stories' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Load more articles' }))

  expect(await screen.findByRole('link', { name: 'Second story' })).toBeTruthy()
  expect(await screen.findByRole('link', { name: 'Fire contained' })).toBeTruthy()
  expect(api.eventClusters).toHaveBeenLastCalledWith('ev-1', 'cc')
  expect(api.eventArticles).toHaveBeenLastCalledWith('ev-1', 'ac')
  await waitFor(() => expect(screen.queryByRole('button', { name: /Load more/ })).toBeNull())
})
