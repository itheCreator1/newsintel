import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../../lib/api'
import type { CompareResponse } from '../../lib/api-types'
import { navigationHarness, resetNavigationHarness } from '../../test/navigation-harness'
import { renderWithQuery } from '../../test/render'
import ComparePage from './page'

vi.mock('../../lib/api', async importOriginal => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: { compare: vi.fn(), compareArticles: vi.fn(), compareStories: vi.fn(), nlpEntities: vi.fn(), feeds: vi.fn() },
}))
vi.mock('../../components/BarChart', () => ({
  BarChart: ({ items, ariaLabel, onSelect }: { items: { id: string; label: string }[]; ariaLabel: string; onSelect(item: { id: string }): void }) => (
    <div data-testid="timeline" aria-label={ariaLabel}>{items.map(item => <button key={item.id} onClick={() => onSelect(item)}>{`${ariaLabel} ${item.label}`}</button>)}</div>
  ),
}))

const subject = (ref: string, label: string, over = {}) => ({ kind: 'entity', ref, label, role: null, retired: false, ...over })
const side = (ref: string, label: string, over = {}) => ({ subject: subject(ref, label), articles: 3, stories: 3, sources: 2, timeline: [{ date: '2026-09-20', article_count: 2 }, { date: '2026-09-21', article_count: 1 }], ...over })
const overlap = (only_a: number, both: number, only_b: number) => ({ only_a, both, only_b, union: only_a + both + only_b, jaccard: only_a + both + only_b ? both / (only_a + both + only_b) : null })
const summary = (over = {}) => ({
  kind: 'entity', role: null, window_days: 30,
  a: side('e1', 'Ada Lovelace'), b: side('e2', 'Charles Babbage', { stories: 2 }),
  overlap: { articles: overlap(1, 2, 1), stories: overlap(1, 2, 0), sources: overlap(0, 2, 0) },
  related: {
    entities: [{ id: 'e3', label: 'Royal Society', a_articles: 1, b_articles: 1 }],
    countries: [{ country_code: 'GB', role: 'primary', a_articles: 2, b_articles: 2 }, { country_code: 'GB', role: 'mentioned', a_articles: 1, b_articles: 0 }],
    sources: [{ id: 's1', label: 'The Wire', a_articles: 3, b_articles: 2 }],
  }, ...over,
} as unknown as CompareResponse)
const article = (id: string, title: string) => ({ id, title, original_url: `https://x/${id}`, normalized_url: `https://x/${id}`, published_at: '2026-09-18T10:00:00Z', first_discovered_at: '2026-09-18T11:00:00Z', provenance: [] })
const story = (id: string, title: string, over = {}) => ({ id, article_count: 3, source_count: 2, first_published_at: null, last_published_at: null, representative_article: article(`rep-${id}`, title), a_articles: 1, b_articles: 2, ...over })

beforeEach(() => {
  vi.clearAllMocks()
  resetNavigationHarness({ pathname: '/compare/', search: 'kind=entity&a=e1&b=e2' })
  vi.mocked(api.compare).mockResolvedValue(summary())
  vi.mocked(api.compareArticles).mockResolvedValue({ items: [article('a1', 'Engine notes')], next_cursor: null })
  vi.mocked(api.compareStories).mockResolvedValue({ items: [story('c1', 'Analytical engine story')], next_cursor: null })
  vi.mocked(api.nlpEntities).mockResolvedValue({ items: [{ id: 'e9', text: 'Grace Hopper', normalized_text: 'grace hopper', kind: 'PERSON' }], next_cursor: null })
  vi.mocked(api.feeds).mockResolvedValue({ items: [{ id: 's1', name: 'The Wire' }, { id: 's2', name: 'Daily Post' }] as never, next_cursor: null })
})
afterEach(cleanup)

it('asks for two subjects, then a second one, without calling the API', () => {
  resetNavigationHarness({ pathname: '/compare/' })
  const { unmount } = renderWithQuery(() => <ComparePage />)
  expect(screen.getByText('Choose two entities to compare.')).toBeTruthy()
  unmount()

  resetNavigationHarness({ pathname: '/compare/', search: 'kind=entity&a=e1' })
  renderWithQuery(() => <ComparePage />)
  expect(screen.getByText('Choose a second entity.')).toBeTruthy()
  expect(api.compare).not.toHaveBeenCalled()
})

it('refuses a subject compared with itself and a country that is not a code, before asking the API', () => {
  resetNavigationHarness({ pathname: '/compare/', search: 'kind=country&a=gr&b=GR' })
  const { unmount } = renderWithQuery(() => <ComparePage />)
  expect(screen.getByText('Choose two different countries.')).toBeTruthy()
  unmount()

  resetNavigationHarness({ pathname: '/compare/', search: 'kind=country&a=GRC&b=GR' })
  renderWithQuery(() => <ComparePage />)
  expect(screen.getByText('A country is a two-letter code.')).toBeTruthy()
  expect(api.compare).not.toHaveBeenCalled()
})

it('reports a missing subject and other failures', async () => {
  vi.mocked(api.compare).mockRejectedValue(new ApiError('Subject not found', 404))
  const { unmount } = renderWithQuery(() => <ComparePage />)
  expect(await screen.findByText('One of these subjects was not found.')).toBeTruthy()
  unmount()

  vi.mocked(api.compare).mockRejectedValue(new ApiError('Request failed', 500))
  renderWithQuery(() => <ComparePage />)
  expect(await screen.findByText('Could not load this comparison.')).toBeTruthy()
})

it('shows both subjects with counts over the window and links to their dossiers', async () => {
  renderWithQuery(() => <ComparePage />)

  expect(await screen.findByRole('heading', { name: 'Ada Lovelace' })).toBeTruthy()
  expect(screen.getByRole('heading', { name: 'Charles Babbage' })).toBeTruthy()
  expect(screen.getByText('Last 30 days: 3 articles · 3 stories · 2 sources')).toBeTruthy()
  expect(screen.getByText('Last 30 days: 3 articles · 2 stories · 2 sources')).toBeTruthy()
  expect(screen.getAllByRole('link', { name: 'Open dossier' }).map(link => link.getAttribute('href'))).toEqual(['/entities/?id=e1', '/entities/?id=e2'])
  expect(api.compare).toHaveBeenCalledWith({ kind: 'entity', a: 'e1', b: 'e2', days: 30 })
})

it('states each overlap with its denominator and defines the measure', async () => {
  renderWithQuery(() => <ComparePage />)

  const articles = within(await screen.findByLabelText('Articles overlap'))
  expect(articles.getByText('2 of 4 articles in either set appear in both · Jaccard 0.50 (2 ÷ 4)')).toBeTruthy()
  expect(articles.getByRole('button', { name: 'Only Ada Lovelace: 1' })).toBeTruthy()
  expect(articles.getByRole('button', { name: 'Both: 2' })).toBeTruthy()
  expect(articles.getByRole('button', { name: 'Only Charles Babbage: 1' })).toBeTruthy()
  expect(within(screen.getByLabelText('Stories overlap')).getByText(/2 of 3 stories in either set appear in both · Jaccard 0.67/)).toBeTruthy()
  expect(within(screen.getByLabelText('Sources overlap')).getByText(/2 of 2 sources/)).toBeTruthy()
  expect(screen.getByText(/Jaccard is the shared count divided by the count in either set/)).toBeTruthy()
})

it('says so when neither subject has articles, without a NaN', async () => {
  vi.mocked(api.compare).mockResolvedValue(summary({ overlap: { articles: overlap(0, 0, 0), stories: overlap(0, 0, 0), sources: overlap(0, 0, 0) } }))
  renderWithQuery(() => <ComparePage />)

  expect(await screen.findByText('Neither has articles in this window.')).toBeTruthy()
  expect(within(screen.getByLabelText('Articles overlap')).getByText('No articles in either set.')).toBeTruthy()
  expect(document.body.textContent).not.toMatch(/NaN/)
})

it('leaves out the sources overlap when comparing sources and marks a retired one', async () => {
  resetNavigationHarness({ pathname: '/compare/', search: 'kind=source&a=s1&b=s2' })
  vi.mocked(api.compare).mockResolvedValue(summary({
    kind: 'source', a: side('s1', 'The Wire', { subject: subject('s1', 'The Wire', { kind: 'source' }), sources: null }),
    b: side('s2', 'Daily Post', { subject: subject('s2', 'Daily Post', { kind: 'source', retired: true }), sources: null }),
    overlap: { articles: overlap(2, 1, 1), stories: overlap(1, 2, 0), sources: null }, related: { entities: [], countries: [], sources: null },
  }))
  renderWithQuery(() => <ComparePage />)

  expect(await screen.findByText('Retired. Its archive is kept.')).toBeTruthy()
  expect(screen.getAllByText('Last 30 days: 3 articles · 3 stories')).toHaveLength(2)
  expect(screen.queryByLabelText('Sources overlap')).toBeNull()
  expect(screen.getAllByRole('link', { name: 'Open dossier' })[0].getAttribute('href')).toBe('/sources/detail/?id=s1&from=%2Fcompare%2F%3Fkind%3Dsource%26a%3Ds1%26b%3Ds2')
})

it('lists what is related to either subject with a count for each side', async () => {
  renderWithQuery(() => <ComparePage />)

  const entities = within((await screen.findByText('Royal Society')).closest('table')!)
  expect(entities.getByRole('link', { name: 'Royal Society' }).getAttribute('href')).toBe('/entities/?id=e3')
  expect(entities.getByRole('columnheader', { name: 'Articles with Ada Lovelace' })).toBeTruthy()
  // The annotation role `primary` is shown as the story country, and roles stay separate rows.
  expect(screen.getByText('GB · story')).toBeTruthy()
  expect(screen.getByText('GB · mentioned')).toBeTruthy()
  expect(screen.getByRole('link', { name: 'The Wire' }).getAttribute('href')).toMatch(/^\/sources\/detail\/\?id=s1/)
})

it('opens a search for a day of either subject', async () => {
  renderWithQuery(() => <ComparePage />)

  fireEvent.click(await screen.findByRole('button', { name: 'Charles Babbage: articles per day. Click a bar to search that day\'s articles. 2026-09-20' }))

  expect(navigationHarness.push).toHaveBeenCalledWith('/search/?entity_id=e2&after=2026-09-20&before=2026-09-21')
})

it('shows the shared articles first, then the part chosen from the overlap or the selector', async () => {
  renderWithQuery(() => <ComparePage />)

  expect((await screen.findByRole('link', { name: 'Engine notes' })).getAttribute('href')).toBe('/articles/?article=a1&from=%2Fcompare%2F%3Fkind%3Dentity%26a%3De1%26b%3De2')
  expect(api.compareArticles).toHaveBeenLastCalledWith({ kind: 'entity', a: 'e1', b: 'e2', days: 30 }, 'both', undefined)

  fireEvent.click(within(screen.getByLabelText('Articles overlap')).getByRole('button', { name: 'Only Ada Lovelace: 1' }))
  await waitFor(() => expect(api.compareArticles).toHaveBeenLastCalledWith(expect.anything(), 'a', undefined))

  fireEvent.change(screen.getByLabelText('Show'), { target: { value: 'b' } })
  await waitFor(() => expect(api.compareArticles).toHaveBeenLastCalledWith(expect.anything(), 'b', undefined))
})

it('lists stories with how many articles each subject has in them', async () => {
  renderWithQuery(() => <ComparePage />)
  await screen.findByRole('heading', { name: 'Ada Lovelace' })

  fireEvent.click(screen.getByRole('button', { name: 'Stories' }))

  expect((await screen.findByRole('link', { name: 'Analytical engine story' })).getAttribute('href')).toBe('/clusters/?id=c1&from=%2Fcompare%2F%3Fkind%3Dentity%26a%3De1%26b%3De2')
  expect(screen.getByText('3 articles · 2 sources · 1 from Ada Lovelace · 2 from Charles Babbage')).toBeTruthy()
})

it('loads more evidence and reports an empty part', async () => {
  vi.mocked(api.compareArticles)
    .mockResolvedValueOnce({ items: [article('a1', 'First')], next_cursor: 'next' })
    .mockResolvedValueOnce({ items: [article('a2', 'Second')], next_cursor: null })
  renderWithQuery(() => <ComparePage />)

  fireEvent.click(await screen.findByRole('button', { name: 'Load more articles' }))
  expect(await screen.findByRole('link', { name: 'Second' })).toBeTruthy()
  expect(api.compareArticles).toHaveBeenLastCalledWith(expect.anything(), 'both', 'next')

  vi.mocked(api.compareArticles).mockResolvedValue({ items: [], next_cursor: null })
  fireEvent.change(screen.getByLabelText('Show'), { target: { value: 'b' } })
  expect(await screen.findByText('No articles in this part.')).toBeTruthy()
})

it('keeps the whole comparison in the URL', async () => {
  resetNavigationHarness({ pathname: '/compare/', search: 'kind=country&a=GR&b=FR&role=mentioned&days=90' })
  renderWithQuery(() => <ComparePage />)
  await waitFor(() => expect(api.compare).toHaveBeenCalledWith({ kind: 'country', a: 'GR', b: 'FR', role: 'mentioned', days: 90 }))

  fireEvent.click(screen.getByRole('button', { name: 'Swap' }))
  expect(navigationHarness.replace).toHaveBeenLastCalledWith('/compare/?kind=country&a=FR&b=GR&role=mentioned&days=90')
  fireEvent.change(screen.getByLabelText('Country meaning'), { target: { value: 'source' } })
  expect(navigationHarness.replace).toHaveBeenLastCalledWith('/compare/?kind=country&a=GR&b=FR&role=source&days=90')
  fireEvent.change(screen.getByLabelText('Window'), { target: { value: '7' } })
  expect(navigationHarness.replace).toHaveBeenLastCalledWith('/compare/?kind=country&a=GR&b=FR&role=mentioned&days=7')
})

it('clears both subjects when the type changes', async () => {
  renderWithQuery(() => <ComparePage />)
  await screen.findByRole('heading', { name: 'Ada Lovelace' })

  fireEvent.change(screen.getByLabelText('Compare'), { target: { value: 'source' } })

  expect(navigationHarness.replace).toHaveBeenLastCalledWith('/compare/?kind=source')
})

it('picks an entity by searching for it', async () => {
  resetNavigationHarness({ pathname: '/compare/', search: 'kind=entity' })
  renderWithQuery(() => <ComparePage />)

  fireEvent.change(screen.getByLabelText('Entity A'), { target: { value: 'grace' } })
  fireEvent.click(await screen.findByRole('button', { name: 'Grace Hopper (PERSON)' }))

  expect(api.nlpEntities).toHaveBeenCalledWith('grace')
  expect(navigationHarness.replace).toHaveBeenLastCalledWith('/compare/?kind=entity&a=e9')
})

it('picks a source from the list and a country by its code', async () => {
  resetNavigationHarness({ pathname: '/compare/', search: 'kind=source' })
  const { unmount } = renderWithQuery(() => <ComparePage />)

  const chooser = await screen.findByLabelText('Source B')
  await screen.findAllByRole('option', { name: 'Daily Post' })
  fireEvent.change(chooser, { target: { value: 's2' } })
  expect(navigationHarness.replace).toHaveBeenLastCalledWith('/compare/?kind=source&b=s2')
  unmount()

  resetNavigationHarness({ pathname: '/compare/', search: 'kind=country' })
  renderWithQuery(() => <ComparePage />)
  fireEvent.change(screen.getByLabelText('Country A'), { target: { value: 'gr' } })
  expect(navigationHarness.replace).toHaveBeenLastCalledWith('/compare/?kind=country&a=GR&role=story')
})

it('never ranks the subjects or names a winner', async () => {
  renderWithQuery(() => <ComparePage />)
  await screen.findByRole('heading', { name: 'Ada Lovelace' })

  expect(document.body.textContent).not.toMatch(/\b(winner|wins|best|score|ranking|leads?|ahead)\b/i)
})
