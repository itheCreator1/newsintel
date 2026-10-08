import { cleanup, fireEvent, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../../lib/api'
import type { SeeAlsoLabel } from '../../lib/api-types'
import { navigationHarness, resetNavigationHarness } from '../../test/navigation-harness'
import { renderWithQuery } from '../../test/render'
import EntitiesPage from './page'

vi.mock('../../lib/api', async importOriginal => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: {
    entityDossier: vi.fn(), entityArticles: vi.fn(), entityClusters: vi.fn(), entityRelationships: vi.fn(), createMonitor: vi.fn(),
    entityVariants: vi.fn(), entityHistory: vi.fn(), mergeEntity: vi.fn(), splitEntity: vi.fn(), updateEntity: vi.fn(),
    addDistinct: vi.fn(), nlpEntities: vi.fn(), seeAlso: vi.fn(), addRelation: vi.fn(), updateRelation: vi.fn(), removeRelation: vi.fn(),
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
  vi.mocked(api.seeAlso).mockResolvedValue({ labels: ['member_of', 'leader_of', 'related'], items: [] })
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
    { id: 'h3', action: 'relation_added', entity_id: 'ent-1', other_id: 'ent-8', before: null, after: { relation_type: 'leader_of' }, created_at: '2026-10-08T12:00:00Z' },
    { id: 'h4', action: 'relation_removed', entity_id: 'ent-1', other_id: 'ent-8', before: { relation_type: 'leader_of' }, after: null, created_at: '2026-10-08T13:00:00Z' },
  ] })
  renderWithQuery(() => <EntitiesPage />)

  const history = await screen.findByRole('list', { name: 'Authority history' })
  const items = within(history).getAllByRole('listitem').map(item => item.textContent)
  expect(items[0]).toContain('Merged')
  expect(items[1]).toContain('Renamed to Obama, Barack')
  expect(items[2]).toContain('Added a see-also link')
  expect(items[3]).toContain('Removed a see-also link')
})

const link = (id: string, label: SeeAlsoLabel, name: string, over = {}) => ({
  id, label, entity: { id: `ent-${name}`, display_name: name, entity_type: 'ORG' }, valid_from: null, valid_to: null, note: null, source_article: null, ...over,
})

it('lists the see-also links by type, with dates, notes and the source article', async () => {
  vi.mocked(api.seeAlso).mockResolvedValue({ labels: ['member_of', 'leader_of', 'related'], items: [
    link('r3', 'member_of', 'NATO', { valid_from: '1952', note: 'Founding member', source_article: { id: 'a7', title: 'Treaty signed' } }),
    link('r1', 'earlier_name', 'Facebook', { valid_to: '2021-10' }),
    link('r4', 'related', 'Michelle Obama', { valid_from: '1992', valid_to: '2024' }),
    link('r2', 'member_of', 'EU', { valid_from: '1981' }),
  ] })
  renderWithQuery(() => <EntitiesPage />)

  const list = await screen.findByRole('list', { name: 'See also' })
  expect(within(list).getAllByRole('listitem').map(item => item.textContent)).toEqual([
    expect.stringContaining('Earlier name: Facebook (until 2021-10)'),
    expect.stringContaining('Member of: EU (from 1981)'),
    expect.stringContaining('Member of: NATO (from 1952) · Founding member · Source: Treaty signed'),
    expect.stringContaining('Related: Michelle Obama (1992 – 2024)'),
  ])
  expect(within(list).getByRole('link', { name: 'NATO' })).toHaveAttribute('href', '/entities/?id=ent-NATO')
  expect(within(list).getByRole('link', { name: 'Treaty signed' })).toHaveAttribute('href', '/articles/?article=a7')
  expect(api.seeAlso).toHaveBeenCalledWith('ent-1')
})

it('says when there are no see-also links', async () => {
  renderWithQuery(() => <EntitiesPage />)

  expect(await screen.findByText('No see-also links yet.')).toBeTruthy()
})

it('adds a link of a type this entity can take, to an entity picked by name', async () => {
  vi.mocked(api.nlpEntities).mockResolvedValue({ items: [{ id: 'ent-8', kind: 'ORG', normalized_text: 'democratic party', text: 'Democratic Party' }], next_cursor: null })
  vi.mocked(api.addRelation).mockResolvedValue(link('r9', 'leader_of', 'Democratic Party'))
  renderWithQuery(() => <EntitiesPage />)

  fireEvent.click(await screen.findByRole('button', { name: 'Add link' }))
  const type = screen.getByLabelText('Link type')
  expect(within(type).getAllByRole('option').map(option => option.textContent)).toEqual(['Member of', 'Leader of', 'Related'])
  fireEvent.change(type, { target: { value: 'leader_of' } })
  fireEvent.change(screen.getByLabelText('Find the linked entity'), { target: { value: 'democratic' } })
  fireEvent.click(await screen.findByRole('button', { name: 'Democratic Party (ORG)' }))
  fireEvent.change(screen.getByLabelText('From'), { target: { value: '2009' } })
  fireEvent.change(screen.getByLabelText('Link note'), { target: { value: ' Party chair ' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save link' }))

  await vi.waitFor(() => expect(api.addRelation).toHaveBeenCalledWith('ent-1', { label: 'leader_of', target_id: 'ent-8', valid_from: '2009', note: 'Party chair' }))
  await vi.waitFor(() => expect(api.seeAlso).toHaveBeenCalledTimes(2))
  await vi.waitFor(() => expect(api.entityHistory).toHaveBeenCalledTimes(2))
})

it('shows why a link was refused', async () => {
  vi.mocked(api.seeAlso).mockResolvedValue({ labels: ['related'], items: [] })
  vi.mocked(api.nlpEntities).mockResolvedValue({ items: [{ id: 'ent-8', kind: 'ORG', normalized_text: 'acme', text: 'Acme' }], next_cursor: null })
  vi.mocked(api.addRelation).mockRejectedValue(new ApiError('A related link needs a note saying how they relate', 422))
  renderWithQuery(() => <EntitiesPage />)

  fireEvent.click(await screen.findByRole('button', { name: 'Add link' }))
  fireEvent.change(screen.getByLabelText('Find the linked entity'), { target: { value: 'acme' } })
  fireEvent.click(await screen.findByRole('button', { name: 'Acme (ORG)' }))
  fireEvent.click(screen.getByRole('button', { name: 'Save link' }))

  expect(await screen.findByText('A related link needs a note saying how they relate')).toBeTruthy()
})

it('edits the dates and note of a link, and removes one', async () => {
  vi.mocked(api.seeAlso).mockResolvedValue({ labels: ['member_of', 'leader_of', 'related'], items: [
    link('r3', 'member_of', 'NATO', { valid_from: '1952', note: 'Founding member' }),
    link('r1', 'earlier_name', 'Facebook'),
  ] })
  vi.mocked(api.updateRelation).mockResolvedValue(link('r3', 'member_of', 'NATO'))
  vi.mocked(api.removeRelation).mockResolvedValue(undefined)
  renderWithQuery(() => <EntitiesPage />)

  fireEvent.click(await screen.findByRole('button', { name: 'Edit link to NATO' }))
  expect(screen.getByLabelText('From')).toHaveValue('1952')
  fireEvent.change(screen.getByLabelText('Until'), { target: { value: '2004' } })
  fireEvent.change(screen.getByLabelText('Link note'), { target: { value: '' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save link' }))
  await vi.waitFor(() => expect(api.updateRelation).toHaveBeenCalledWith('r3', { valid_from: '1952', valid_to: '2004', note: null }))

  fireEvent.click(await screen.findByRole('button', { name: 'Remove link to Facebook' }))
  await vi.waitFor(() => expect(api.removeRelation).toHaveBeenCalledWith('r1'))
})
