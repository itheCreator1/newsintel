import { cleanup, fireEvent, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../../lib/api'
import { navigationHarness, resetNavigationHarness } from '../../test/navigation-harness'
import { renderWithQuery } from '../../test/render'
import EntitiesPage from './page'

vi.mock('../../lib/api', async importOriginal => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: {
    entityDossier: vi.fn(), entityArticles: vi.fn(), entityClusters: vi.fn(), entityRelationships: vi.fn(), createMonitor: vi.fn(),
    entityVariants: vi.fn(), entityHistory: vi.fn(), mergeEntity: vi.fn(), splitEntity: vi.fn(), updateEntity: vi.fn(),
    addDistinct: vi.fn(), nlpEntities: vi.fn(),
  },
}))
vi.mock('../../components/BarChart', () => ({
  BarChart: ({ items, onSelect }: { items: { id: string; label: string }[]; onSelect(item: { id: string }): void }) => (
    <div data-testid="timeline">{items.map(item => <button key={item.id} onClick={() => onSelect(item)}>{item.label}</button>)}</div>
  ),
}))

const dossier = (over = {}) => ({
  id: 'ent-1', display_name: 'Barack Obama', normalized_text: 'barack obama', language: 'en', entity_type: 'PERSON',
  redirected_from: null, preferred_text: null, status: 'provisional' as const, ambiguous: false, note: null,
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
  vi.mocked(api.entityVariants).mockResolvedValue({ items: [] })
  vi.mocked(api.entityHistory).mockResolvedValue({ items: [] })
})
afterEach(cleanup)

it('links to the events this entity characterises', async () => {
  renderWithQuery(() => <EntitiesPage />)

  expect(await screen.findByRole('link', { name: 'Events with this entity' })).toHaveAttribute('href', '/events/?entity_id=ent-1')
  expect(screen.getByRole('link', { name: 'Compare with another entity' })).toHaveAttribute('href', '/compare/?kind=entity&a=ent-1')
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

it('watches this entity under its name', async () => {
  vi.mocked(api.createMonitor).mockResolvedValueOnce({} as never)
  renderWithQuery(() => <EntitiesPage />)

  fireEvent.click(await screen.findByRole('button', { name: 'Watch entity' }))
  await vi.waitFor(() => expect(api.createMonitor).toHaveBeenCalledWith('Barack Obama', expect.objectContaining({ entity_id: ['ent-1'], source_id: [] }), 'entity'))
  expect(await screen.findByRole('link', { name: 'Open watchlist' })).toHaveAttribute('href', '/monitors/')
})

const variant = { id: 'var-1', display_name: 'B. Obama', normalized_text: 'b obama', language: 'en', entity_type: 'PERSON' }
const run = (kind: 'merge' | 'split', entity: string, root: string) => ({ id: `run-${kind}`, kind, status: 'running' as const, entity_id: entity, root_id: root })
const authority = (over = {}) => ({ id: 'ent-1', display_name: 'Barack Obama', preferred_text: null, status: 'provisional' as const, ambiguous: false, note: null, authority_id: null, ...over })

it('shows the status, the variants and says when it was opened from a merged name', async () => {
  vi.mocked(api.entityDossier).mockResolvedValue(dossier({ redirected_from: 'var-1', status: 'established' }))
  vi.mocked(api.entityVariants).mockResolvedValue({ items: [variant] })
  renderWithQuery(() => <EntitiesPage />)

  expect(await screen.findByText('Opened from a name merged into this entity.')).toBeTruthy()
  expect(screen.getByText('Established')).toBeTruthy()
  const variants = await screen.findByRole('list', { name: 'Other names' })
  expect(within(variants).getByText('B. Obama')).toBeTruthy()
  expect(api.entityVariants).toHaveBeenCalledWith('ent-1')
})

it('merges this entity into another one picked by name and opens the result', async () => {
  vi.mocked(api.nlpEntities).mockResolvedValue({ items: [{ id: 'ent-9', kind: 'PERSON', normalized_text: 'barack h obama', text: 'Barack H. Obama' }], next_cursor: null })
  vi.mocked(api.mergeEntity).mockResolvedValue(run('merge', 'ent-1', 'ent-9'))
  renderWithQuery(() => <EntitiesPage />)

  fireEvent.click(await screen.findByRole('button', { name: 'Merge with…' }))
  fireEvent.change(screen.getByLabelText('Find the entity to merge into'), { target: { value: 'barack' } })
  fireEvent.click(await screen.findByRole('button', { name: 'Barack H. Obama (PERSON)' }))
  fireEvent.click(screen.getByRole('button', { name: 'Merge into Barack H. Obama' }))

  await vi.waitFor(() => expect(api.mergeEntity).toHaveBeenCalledWith('ent-1', 'ent-9'))
  await vi.waitFor(() => expect(navigationHarness.push).toHaveBeenCalledWith('/entities/?id=ent-9'))
})

it('shows why a merge was refused', async () => {
  vi.mocked(api.nlpEntities).mockResolvedValue({ items: [{ id: 'ent-9', kind: 'PERSON', normalized_text: 'other', text: 'Other Obama' }], next_cursor: null })
  vi.mocked(api.mergeEntity).mockRejectedValue(new ApiError('These entities are marked as different', 409))
  renderWithQuery(() => <EntitiesPage />)

  fireEvent.click(await screen.findByRole('button', { name: 'Merge with…' }))
  fireEvent.change(screen.getByLabelText('Find the entity to merge into'), { target: { value: 'other' } })
  fireEvent.click(await screen.findByRole('button', { name: 'Other Obama (PERSON)' }))
  fireEvent.click(screen.getByRole('button', { name: 'Merge into Other Obama' }))

  expect(await screen.findByText('These entities are marked as different')).toBeTruthy()
  expect(navigationHarness.push).not.toHaveBeenCalled()
})

it('splits a variant back out', async () => {
  vi.mocked(api.entityVariants).mockResolvedValue({ items: [variant] })
  vi.mocked(api.splitEntity).mockResolvedValue(run('split', 'var-1', 'ent-1'))
  renderWithQuery(() => <EntitiesPage />)

  fireEvent.click(await screen.findByRole('button', { name: 'Split B. Obama' }))

  await vi.waitFor(() => expect(api.splitEntity).toHaveBeenCalledWith('var-1'))
  expect(await screen.findByText('B. Obama is its own entity again; its articles move back shortly.')).toBeTruthy()
})

it('renames the entity, marks it established and keeps a note', async () => {
  vi.mocked(api.updateEntity).mockResolvedValue(authority())
  renderWithQuery(() => <EntitiesPage />)

  fireEvent.click(await screen.findByRole('button', { name: 'Rename' }))
  fireEvent.change(screen.getByLabelText('Preferred name'), { target: { value: 'Obama, Barack' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save name' }))
  await vi.waitFor(() => expect(api.updateEntity).toHaveBeenCalledWith('ent-1', { preferred_text: 'Obama, Barack' }))

  fireEvent.click(screen.getByRole('button', { name: 'Mark established' }))
  await vi.waitFor(() => expect(api.updateEntity).toHaveBeenCalledWith('ent-1', { status: 'established' }))

  fireEvent.click(screen.getByLabelText('Ambiguous name (may stand for several people)'))
  await vi.waitFor(() => expect(api.updateEntity).toHaveBeenCalledWith('ent-1', { ambiguous: true }))

  fireEvent.change(screen.getByLabelText('Note'), { target: { value: '44th US president' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save note' }))
  await vi.waitFor(() => expect(api.updateEntity).toHaveBeenCalledWith('ent-1', { note: '44th US president' }))
})

it('records that another entity is not the same', async () => {
  vi.mocked(api.nlpEntities).mockResolvedValue({ items: [{ id: 'ent-7', kind: 'PERSON', normalized_text: 'michelle obama', text: 'Michelle Obama' }], next_cursor: null })
  vi.mocked(api.addDistinct).mockResolvedValue(undefined)
  renderWithQuery(() => <EntitiesPage />)

  fireEvent.click(await screen.findByRole('button', { name: 'Not the same as…' }))
  fireEvent.change(screen.getByLabelText('Find the entity that is different'), { target: { value: 'michelle' } })
  fireEvent.click(await screen.findByRole('button', { name: 'Michelle Obama (PERSON)' }))
  fireEvent.click(screen.getByRole('button', { name: 'Mark Michelle Obama as different' }))

  await vi.waitFor(() => expect(api.addDistinct).toHaveBeenCalledWith('ent-1', 'ent-7'))
  expect(await screen.findByText('Michelle Obama is recorded as a different entity.')).toBeTruthy()
})

it('lists the authority history, oldest first', async () => {
  vi.mocked(api.entityHistory).mockResolvedValue({ items: [
    { id: 'h1', action: 'merged', entity_id: 'var-1', other_id: 'ent-1', before: { authority_id: null }, after: { authority_id: 'ent-1' }, created_at: '2026-10-08T10:00:00Z' },
    { id: 'h2', action: 'renamed', entity_id: 'ent-1', other_id: null, before: { preferred_text: null }, after: { preferred_text: 'Obama, Barack' }, created_at: '2026-10-08T11:00:00Z' },
  ] })
  renderWithQuery(() => <EntitiesPage />)

  const history = await screen.findByRole('list', { name: 'Authority history' })
  const items = within(history).getAllByRole('listitem').map(item => item.textContent)
  expect(items[0]).toContain('Merged')
  expect(items[1]).toContain('Renamed to Obama, Barack')
})
