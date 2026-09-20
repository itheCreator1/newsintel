import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../../../lib/api'
import { navigationHarness, resetNavigationHarness } from '../../../test/navigation-harness'
import { renderWithQuery } from '../../../test/render'
import SourceDetailPage from './page'

vi.mock('../../../lib/api', async importOriginal => ({
  ...(await importOriginal<typeof import('../../../lib/api')>()),
  api: { source: vi.fn(), sourceCoverage: vi.fn(), sourceTiming: vi.fn(), sourceArticles: vi.fn(), sourceClusters: vi.fn(), sourceFetches: vi.fn() },
}))
vi.mock('../../../components/BarChart', () => ({
  BarChart: ({ items, onSelect }: { items: { id: string; label: string }[]; onSelect(item: { id: string }): void }) => (
    <div data-testid="timeline">{items.map(item => <button key={item.id} onClick={() => onSelect(item)}>{item.label}</button>)}</div>
  ),
}))

const detail = (over = {}) => ({
  id: 's-1', name: 'Relationships Wire', url: 'https://wire.example/rss', source_country: 'GR', expected_language: 'el', tags: [], enabled: true,
  poll_interval_minutes: 30, fetching_mode: 'full_text', next_poll_at: '2026-09-21T10:30:00Z', last_success_at: '2026-09-21T10:00:00Z', created_at: '2026-08-01T00:00:00Z',
  retired_at: null, first_seen_at: '2026-08-02T00:00:00Z', last_seen_at: '2026-09-21T09:00:00Z',
  health: { last_attempt_at: '2026-09-21T10:00:00Z', last_attempt_status: 'success', last_failure: null, consecutive_failures: 0 },
  window_days: 30,
  publishing: { articles: 230, with_published_at: 212 },
  extraction: { articles: 230, extracted: 200, failed: 10, in_progress: 5, not_extracted: 15 },
  fetches: { total: 40, success: 37, failed: 3, new_articles: 120, mean_duration_ms: 250.4, failures_by_category: { timeout: 2, network: 1 } },
  timeline: [{ date: '2026-09-20', article_count: 4 }, { date: '2026-09-21', article_count: 0 }], ...over,
})
const article = (id: string, title: string) => ({ id, title, original_url: `https://x/${id}`, normalized_url: `https://x/${id}`, published_at: '2026-09-18T10:00:00Z', first_discovered_at: '2026-09-18T11:00:00Z', provenance: [] })
const story = (id: string, title: string, over = {}) => ({
  id, article_count: 3, source_count: 2, first_published_at: '2026-09-17T00:00:00Z', last_published_at: '2026-09-18T00:00:00Z',
  representative_article: article(`rep-${id}`, title), source_article_id: `mine-${id}`, first: false, minutes_behind: 60.4, ...over,
})
const fetchRow = (id: string, over = {}) => ({ id, feed_id: 's-1', status: 'success', attempt_count: 1, http_status: 200, duration_ms: 100, entry_count: 9, invalid_entry_count: 0, new_article_count: 4, error_category: null, error_message: null, started_at: '2026-09-21T10:00:00Z', completed_at: '2026-09-21T10:00:01Z', ...over })

beforeEach(() => {
  vi.clearAllMocks()
  resetNavigationHarness({ pathname: '/sources/detail/', search: 'id=s-1&from=%2Fentities%2F%3Fid%3De1' })
  vi.mocked(api.source).mockResolvedValue(detail())
  vi.mocked(api.sourceCoverage).mockResolvedValue({
    window_days: 30, articles: 230,
    entities: [{ id: 'ent-1', display_name: 'Barack Obama', normalized_text: 'barack obama', language: 'en', entity_type: 'PERSON', article_count: 9 }],
    countries: [{ country_code: 'GR', role: 'primary', article_count: 50 }, { country_code: 'FR', role: 'mentioned', article_count: 7 }],
    languages: [{ language: 'el', article_count: 200 }],
  })
  vi.mocked(api.sourceTiming).mockResolvedValue({ window_days: 30, stories: 6, first: 3, median_minutes_behind: 60, p90_minutes_behind: 108 })
  vi.mocked(api.sourceClusters).mockResolvedValue({ items: [story('c1', 'Story headline'), story('c2', 'Lead story', { first: true, minutes_behind: 0 }), story('c3', 'Solo story', { source_count: 1, first: null, minutes_behind: null })], next_cursor: null })
  vi.mocked(api.sourceArticles).mockResolvedValue({ items: [article('a1', 'Fire spreads')], next_cursor: null })
  vi.mocked(api.sourceFetches).mockResolvedValue({ items: [fetchRow('f1'), fetchRow('f2', { status: 'failed', new_article_count: 0, error_category: 'timeout', error_message: 'Timed out' })], next_cursor: null })
})
afterEach(cleanup)

it('asks for a source when there is no id', () => {
  resetNavigationHarness({ pathname: '/sources/detail/' })
  renderWithQuery(() => <SourceDetailPage />)

  expect(screen.getByText(/Choose a source/)).toBeTruthy()
  expect(api.source).not.toHaveBeenCalled()
})

it('reports a missing source once and does not load its evidence', async () => {
  vi.mocked(api.source).mockRejectedValue(new ApiError('Source not found', 404))
  renderWithQuery(() => <SourceDetailPage />)

  expect(await screen.findByText('Source not found.')).toBeTruthy()
  for (const call of [api.sourceCoverage, api.sourceTiming, api.sourceClusters, api.sourceArticles, api.sourceFetches]) expect(call).not.toHaveBeenCalled()
})

it('reports other failures', async () => {
  vi.mocked(api.source).mockRejectedValue(new ApiError('Request failed', 500))
  renderWithQuery(() => <SourceDetailPage />)

  expect(await screen.findByText('Could not load this source.')).toBeTruthy()
})

it('shows the identity, health and the origin link', async () => {
  renderWithQuery(() => <SourceDetailPage />)

  expect(await screen.findByRole('heading', { name: 'Relationships Wire' })).toBeTruthy()
  expect(screen.getByText('Healthy')).toBeTruthy()
  expect(screen.getByText(/Country GR · Language el · full text · polls every 30 min/)).toBeTruthy()
  expect(screen.getByText(/Last attempt success/)).toBeTruthy()
  expect(screen.getByRole('link', { name: 'Back to entity' }).getAttribute('href')).toBe('/entities/?id=e1')
})

it('says so when the source has been retired or is failing', async () => {
  vi.mocked(api.source).mockResolvedValue(detail({
    enabled: false, retired_at: '2026-09-20T00:00:00Z',
    health: { last_attempt_at: '2026-09-20T10:00:00Z', last_attempt_status: 'failed', consecutive_failures: 3, last_failure: fetchRow('f9', { status: 'failed', error_category: 'timeout', error_message: 'Timed out' }) },
  }))
  renderWithQuery(() => <SourceDetailPage />)

  expect(await screen.findByText(/Retired/)).toBeTruthy()
  expect(screen.getByText('3 failed fetches in a row')).toBeTruthy()
  expect(screen.getByText('Last failure: timeout — Timed out')).toBeTruthy()
})

it('shows each metric with its denominator', async () => {
  renderWithQuery(() => <SourceDetailPage />)

  expect(await screen.findByText('212 of 230 articles have a publish date')).toBeTruthy()
  expect(screen.getByText('Of 230 articles: 200 extracted · 10 failed · 5 in progress · 15 not extracted')).toBeTruthy()
  expect(screen.getByText('40 fetches: 37 succeeded · 3 failed · 120 new articles · mean 250 ms')).toBeTruthy()
  expect(screen.getByText('Failures: timeout 2 · network 1')).toBeTruthy()
})

it('reloads the metrics for another window', async () => {
  renderWithQuery(() => <SourceDetailPage />)
  await screen.findByRole('heading', { name: 'Relationships Wire' })

  fireEvent.change(screen.getByLabelText('Window'), { target: { value: '90' } })

  // Coverage and timing wait for the new header, so they follow it.
  await waitFor(() => expect(api.source).toHaveBeenLastCalledWith('s-1', 90))
  await waitFor(() => expect(api.sourceCoverage).toHaveBeenLastCalledWith('s-1', 90))
  await waitFor(() => expect(api.sourceTiming).toHaveBeenLastCalledWith('s-1', 90))
})

it('opens a search for a day of the publishing timeline', async () => {
  renderWithQuery(() => <SourceDetailPage />)

  fireEvent.click(await screen.findByRole('button', { name: '2026-09-20' }))

  expect(navigationHarness.push).toHaveBeenCalledWith('/search/?source_id=s-1&after=2026-09-20&before=2026-09-21')
})

it('lists coverage with the country roles kept apart', async () => {
  renderWithQuery(() => <SourceDetailPage />)

  expect((await screen.findByRole('link', { name: /Barack Obama/ })).getAttribute('href')).toBe('/entities/?id=ent-1')
  expect(screen.getByText('GR · primary · 50 articles')).toBeTruthy()
  expect(screen.getByText('FR · mentioned · 7 articles')).toBeTruthy()
  expect(screen.getByText('el · 200 articles')).toBeTruthy()
})

it('describes the timing position without ranking the source', async () => {
  renderWithQuery(() => <SourceDetailPage />)

  expect(await screen.findByText('First in 3 of 6 shared stories')).toBeTruthy()
  expect(screen.getByText('Otherwise behind the first article by a median of 60 min (90th percentile 108 min)')).toBeTruthy()
})

it('says when there are no shared stories', async () => {
  vi.mocked(api.sourceTiming).mockResolvedValue({ window_days: 30, stories: 0, first: 0, median_minutes_behind: null, p90_minutes_behind: null })
  renderWithQuery(() => <SourceDetailPage />)

  expect(await screen.findByText('No stories shared with another source in this window.')).toBeTruthy()
})

it('lists stories with this source’s position and links them to the story page', async () => {
  renderWithQuery(() => <SourceDetailPage />)

  expect((await screen.findByRole('link', { name: 'Story headline' })).getAttribute('href')).toMatch(/^\/clusters\/\?id=c1&from=%2Fsources%2Fdetail%2F/)
  expect(screen.getByText('60 min behind the first article')).toBeTruthy()
  expect(screen.getByText('First to publish')).toBeTruthy()
  expect(screen.getByText('Only this source')).toBeTruthy()
})

it('lists articles that open the article page with this page as the origin', async () => {
  renderWithQuery(() => <SourceDetailPage />)

  expect((await screen.findByRole('link', { name: 'Fire spreads' })).getAttribute('href')).toMatch(/^\/articles\/\?article=a1&from=%2Fsources%2Fdetail%2F/)
})

it('shows the fetch history with failures', async () => {
  renderWithQuery(() => <SourceDetailPage />)

  const history = await screen.findByRole('region', { name: 'Fetch history' })
  expect(await within(history).findByText('4 new / 9 entries')).toBeTruthy()
  expect(within(history).getByText('timeout: Timed out')).toBeTruthy()
})

it('loads more of each list by cursor and stops at the end', async () => {
  vi.mocked(api.sourceClusters).mockResolvedValueOnce({ items: [story('c1', 'Story headline')], next_cursor: 'cc' }).mockResolvedValueOnce({ items: [story('c9', 'Later story')], next_cursor: null })
  vi.mocked(api.sourceArticles).mockResolvedValueOnce({ items: [article('a1', 'Fire spreads')], next_cursor: 'ac' }).mockResolvedValueOnce({ items: [article('a9', 'Later article')], next_cursor: null })
  vi.mocked(api.sourceFetches).mockResolvedValueOnce({ items: [fetchRow('f1')], next_cursor: 'fc' }).mockResolvedValueOnce({ items: [fetchRow('f9', { new_article_count: 7 })], next_cursor: null })
  renderWithQuery(() => <SourceDetailPage />)

  fireEvent.click(await screen.findByRole('button', { name: 'Load more stories' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Load more articles' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Load more fetches' }))

  expect(await screen.findByRole('link', { name: 'Later story' })).toBeTruthy()
  expect(await screen.findByRole('link', { name: 'Later article' })).toBeTruthy()
  expect(await screen.findByText('7 new / 9 entries')).toBeTruthy()
  expect(api.sourceClusters).toHaveBeenLastCalledWith('s-1', 'cc')
  expect(api.sourceArticles).toHaveBeenLastCalledWith('s-1', 'ac')
  expect(api.sourceFetches).toHaveBeenLastCalledWith('s-1', 'fc')
  expect(screen.queryByRole('button', { name: /Load more/ })).toBeNull()
})

it('starts a comparison from the dossier and labels the way back from one', async () => {
  resetNavigationHarness({ pathname: '/sources/detail/', search: 'id=s-1&from=%2Fcompare%2F%3Fkind%3Dsource%26a%3Ds-1%26b%3Ds-2' })
  renderWithQuery(() => <SourceDetailPage />)

  expect(await screen.findByRole('link', { name: 'Compare with another source' })).toHaveAttribute('href', '/compare/?kind=source&a=s-1')
  expect(screen.getByRole('link', { name: 'Back to comparison' }).getAttribute('href')).toBe('/compare/?kind=source&a=s-1&b=s-2')
})
