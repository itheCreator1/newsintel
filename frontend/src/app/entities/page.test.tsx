import { cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../../lib/api'
import { navigationHarness, resetNavigationHarness } from '../../test/navigation-harness'
import { renderWithQuery } from '../../test/render'
import EntitiesPage from './page'

vi.mock('../../lib/api', async importOriginal => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: { entityDossier: vi.fn(), entityArticles: vi.fn(), entityClusters: vi.fn(), entityRelationships: vi.fn() },
}))
vi.mock('../../components/BarChart', () => ({
  BarChart: ({ items, onSelect }: { items: { id: string; label: string }[]; onSelect(item: { id: string }): void }) => (
    <div data-testid="timeline">{items.map(item => <button key={item.id} onClick={() => onSelect(item)}>{item.label}</button>)}</div>
  ),
}))

const dossier = (over = {}) => ({
  id: 'ent-1', display_name: 'Barack Obama', normalized_text: 'barack obama', language: 'en', entity_type: 'PERSON',
  aliases: [], aliases_status: 'unavailable' as const, total_mentions: 12, article_count: 5, cluster_count: 2,
  first_seen_at: '2026-09-01T00:00:00Z', last_seen_at: '2026-09-18T00:00:00Z', timeline_days: 30,
  timeline: [{ date: '2026-09-17', mentions: 4 }, { date: '2026-09-18', mentions: 8 }], ...over,
})
const article = (id: string, title: string) => ({ id, title, original_url: `https://x/${id}`, normalized_url: `https://x/${id}`, published_at: '2026-09-18T10:00:00Z', first_discovered_at: '2026-09-18T11:00:00Z', provenance: [] })
const articles = (items: ReturnType<typeof article>[], next: string | null = null) => ({ items, next_cursor: next })
const cluster = { id: 'c1', article_count: 3, source_count: 2, first_published_at: '2026-09-17T00:00:00Z', last_published_at: '2026-09-18T00:00:00Z', representative_article: article('a9', 'Story headline') }
const relationships = (over = {}) => ({
  window_days: 30,
  entities: [{ id: 'ent-2', display_name: 'Microsoft', normalized_text: 'microsoft', language: 'en', entity_type: 'ORG', article_count: 3 }],
  countries: [{ country_code: 'US', role: 'mentioned', article_count: 4 }],
  feeds: [{ id: 'f1', name: 'Wire', article_count: 5 }], ...over,
})

beforeEach(() => {
  vi.clearAllMocks()
  resetNavigationHarness({ pathname: '/entities/', search: 'id=ent-1' })
  vi.mocked(api.entityDossier).mockResolvedValue(dossier())
  vi.mocked(api.entityArticles).mockResolvedValue(articles([article('a1', 'First report')]))
  vi.mocked(api.entityClusters).mockResolvedValue({ items: [cluster], next_cursor: null })
  vi.mocked(api.entityRelationships).mockResolvedValue(relationships())
})
afterEach(cleanup)

it('links to the events this entity characterises', async () => {
  renderWithQuery(() => <EntitiesPage />)

  expect(await screen.findByRole('link', { name: 'Events with this entity' })).toHaveAttribute('href', '/events/?entity_id=ent-1')
})

it('shows identity, totals, explicit unavailable aliases, and first/last seen', async () => {
  renderWithQuery(() => <EntitiesPage />)

  expect(await screen.findByRole('heading', { name: 'Barack Obama' })).toBeTruthy()
  expect(screen.getByText('PERSON · en')).toBeTruthy()
  expect(screen.getByText('Aliases unavailable')).toBeTruthy()
  expect(screen.getByText('12 mentions · 5 articles · 2 stories')).toBeTruthy()
  expect(api.entityDossier).toHaveBeenCalledWith('ent-1', 30)
})

it('lists recent articles and stories with return paths and load-more pagination', async () => {
  vi.mocked(api.entityArticles)
    .mockResolvedValueOnce(articles([article('a1', 'First report')], 'next'))
    .mockResolvedValueOnce(articles([article('a2', 'Second report')]))
  renderWithQuery(() => <EntitiesPage />)

  expect(await screen.findByRole('link', { name: 'First report' })).toHaveAttribute('href', '/articles/?article=a1&from=%2Fentities%2F%3Fid%3Dent-1')
  fireEvent.click(screen.getByRole('button', { name: 'Load more articles' }))
  expect(await screen.findByText('Second report')).toBeTruthy()
  expect(screen.getByText('First report')).toBeTruthy()
  expect(api.entityArticles).toHaveBeenLastCalledWith('ent-1', 'next')
  expect(screen.getByRole('link', { name: /Story headline/ })).toHaveAttribute('href', '/clusters/?id=c1&from=%2Fentities%2F%3Fid%3Dent-1')
})

it('renders relationships with dossier links and labelled country roles', async () => {
  renderWithQuery(() => <EntitiesPage />)

  expect(await screen.findByRole('link', { name: /Microsoft/ })).toHaveAttribute('href', '/entities/?id=ent-2')
  expect(screen.getByText('US · mentioned · 4 articles')).toBeTruthy()
  expect(screen.getByRole('link', { name: 'Wire · 5 articles' }).getAttribute('href')).toMatch(/^\/sources\/detail\/\?id=f1&from=%2Fentities%2F/)
})

it('links to a search for the entity and to a dated search from a timeline bar', async () => {
  renderWithQuery(() => <EntitiesPage />)

  expect(await screen.findByRole('link', { name: 'Search articles with this entity' })).toHaveAttribute('href', '/search/?entity_id=ent-1')
  fireEvent.click(screen.getByRole('button', { name: '2026-09-18' }))
  expect(navigationHarness.push).toHaveBeenCalledWith('/search/?entity_id=ent-1&after=2026-09-18&before=2026-09-19')
})

it('changes the window and refetches dossier and relationships', async () => {
  renderWithQuery(() => <EntitiesPage />)
  await screen.findByRole('heading', { name: 'Barack Obama' })

  fireEvent.change(screen.getByLabelText('Window'), { target: { value: '90' } })

  expect(await screen.findByRole('heading', { name: 'Barack Obama' })).toBeTruthy()
  expect(api.entityDossier).toHaveBeenLastCalledWith('ent-1', 90)
  expect(api.entityRelationships).toHaveBeenLastCalledWith('ent-1', 90)
})

it('shows empty states for an entity with no evidence', async () => {
  vi.mocked(api.entityDossier).mockResolvedValue(dossier({ total_mentions: 0, article_count: 0, cluster_count: 0, first_seen_at: null, last_seen_at: null, timeline: [] }))
  vi.mocked(api.entityArticles).mockResolvedValue(articles([]))
  vi.mocked(api.entityClusters).mockResolvedValue({ items: [], next_cursor: null })
  vi.mocked(api.entityRelationships).mockResolvedValue(relationships({ entities: [], countries: [], feeds: [] }))
  renderWithQuery(() => <EntitiesPage />)

  expect(await screen.findByText('No mentions in this window.')).toBeTruthy()
  expect(screen.getByText('No articles yet.')).toBeTruthy()
  expect(screen.getByText('No stories yet.')).toBeTruthy()
  expect(screen.getByText('No related entities.')).toBeTruthy()
})

it('shows not-found for an unknown entity and a generic error otherwise', async () => {
  vi.mocked(api.entityDossier).mockRejectedValue(new ApiError('Entity not found', 404))
  renderWithQuery(() => <EntitiesPage />)
  expect(await screen.findByText('Entity not found.')).toBeTruthy()
  cleanup()

  vi.mocked(api.entityDossier).mockRejectedValue(new Error('boom'))
  renderWithQuery(() => <EntitiesPage />)
  expect(await screen.findByText('Could not load this entity.')).toBeTruthy()
})

it('prompts for an entity when no id is given', async () => {
  resetNavigationHarness({ pathname: '/entities/', search: '' })
  renderWithQuery(() => <EntitiesPage />)

  expect(await screen.findByText('Choose an entity from an article, the graph, or search to open its dossier.')).toBeTruthy()
  expect(api.entityDossier).not.toHaveBeenCalled()
})
