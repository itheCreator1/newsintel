import { cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../lib/api'
import { renderWithQuery } from '../test/render'
import { EdgeEvidencePanel } from './EdgeEvidencePanel'

vi.mock('../lib/api', async importOriginal => ({ ...(await importOriginal<typeof import('../lib/api')>()), api: { edgeEvidence: vi.fn() } }))

const article = (id: string, title: string) => ({ id, title, original_url: `https://x/${id}`, normalized_url: `https://x/${id}`, published_at: '2026-09-14T12:00:00Z', first_discovered_at: '2026-09-14T13:00:00Z', provenance: [] })
const evidence = (over = {}) => ({
  source: { id: 'ent-1', text: 'Harbour Authority', type: 'ORG' }, target: { id: 'ent-2', text: 'Ada Reyes', type: 'PERSON' },
  meaning: 'Both entities are mentioned in the same article. This is co-occurrence, not a stated relationship.',
  article_count: 2, cluster_count: 1, first_at: '2026-09-10T08:00:00Z', last_at: '2026-09-14T12:00:00Z',
  articles: [article('a1', 'Harbour hires Reyes')], next_cursor: null,
  clusters: [{ id: 'c1', edge_article_count: 2, article_count: 5, source_count: 3, representative_article: article('a9', 'Harbour leadership change') }],
  missing_from_archive: 0, ...over,
})
const filters = { q: 'harbour', source_country: ['US'], focus_entity_id: 'ent-1' }
const RETURN = '/graph/?edge=ent-1%3Aent-2'
const renderPanel = () => renderWithQuery(() => <EdgeEvidencePanel source="ent-1" target="ent-2" filters={filters} returnHref={RETURN} />)

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.edgeEvidence).mockResolvedValue(evidence())
})
afterEach(cleanup)

it('explains the edge as co-occurrence and summarises its evidence', async () => {
  renderPanel()

  expect(await screen.findByRole('heading', { name: 'Harbour Authority and Ada Reyes' })).toBeTruthy()
  expect(screen.getByText(/co-occurrence, not a stated relationship/)).toBeTruthy()
  expect(screen.getByText('2 articles · 1 story')).toBeTruthy()
  expect(api.edgeEvidence).toHaveBeenCalledWith({ ...filters, source: 'ent-1', target: 'ent-2' }, undefined)
})

it('links evidence articles and stories back to this graph view and both entities to their dossiers', async () => {
  renderPanel()

  expect(await screen.findByRole('link', { name: 'Harbour hires Reyes' })).toHaveAttribute('href', `/articles/?article=a1&from=${encodeURIComponent(RETURN)}`)
  expect(screen.getByRole('link', { name: /Harbour leadership change/ })).toHaveAttribute('href', `/clusters/?id=c1&from=${encodeURIComponent(RETURN)}`)
  expect(screen.getByText(/2 of these articles/)).toBeTruthy()
  expect(screen.getByRole('link', { name: 'Open dossier for Harbour Authority' })).toHaveAttribute('href', '/entities/?id=ent-1')
  expect(screen.getByRole('link', { name: 'Open dossier for Ada Reyes' })).toHaveAttribute('href', '/entities/?id=ent-2')
})

it('loads further evidence pages with the returned cursor', async () => {
  vi.mocked(api.edgeEvidence)
    .mockResolvedValueOnce(evidence({ next_cursor: 'more' }))
    .mockResolvedValueOnce(evidence({ articles: [article('a2', 'Reyes leads talks')] }))
  renderPanel()

  fireEvent.click(await screen.findByRole('button', { name: 'Load more articles' }))

  expect(await screen.findByText('Reyes leads talks')).toBeTruthy()
  expect(screen.getByText('Harbour hires Reyes')).toBeTruthy()
  expect(api.edgeEvidence).toHaveBeenLastCalledWith({ ...filters, source: 'ent-1', target: 'ent-2' }, 'more')
})

it('says so when articles behind the count are no longer in the archive', async () => {
  vi.mocked(api.edgeEvidence).mockResolvedValue(evidence({ missing_from_archive: 1 }))
  renderPanel()

  expect(await screen.findByText('1 matching article is no longer in the archive; rebuild search to refresh the index.')).toBeTruthy()
})

it('sums archive gaps across pages and says when only the top stories are listed', async () => {
  vi.mocked(api.edgeEvidence)
    .mockResolvedValueOnce(evidence({ next_cursor: 'more', cluster_count: 15, missing_from_archive: 1 }))
    .mockResolvedValueOnce(evidence({ articles: [article('a2', 'Reyes leads talks')], missing_from_archive: 1 }))
  renderPanel()

  expect(await screen.findByText('Showing the 1 stories with the most of these articles.')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Load more articles' }))

  expect(await screen.findByText('2 matching articles are no longer in the archive; rebuild search to refresh the index.')).toBeTruthy()
})

it('shows an empty state when the pair never co-occurs under the filters', async () => {
  vi.mocked(api.edgeEvidence).mockResolvedValue(evidence({ article_count: 0, cluster_count: 0, first_at: null, last_at: null, articles: [], clusters: [] }))
  renderPanel()

  expect(await screen.findByText('No articles contain both entities under these filters.')).toBeTruthy()
})

it('shows loading and distinct error states', async () => {
  vi.mocked(api.edgeEvidence).mockReturnValue(new Promise(() => {}))
  renderPanel()
  expect(await screen.findByText('Loading evidence…')).toBeTruthy()
  cleanup()

  vi.mocked(api.edgeEvidence).mockRejectedValue(new ApiError('Entity not found', 404))
  renderPanel()
  expect(await screen.findByText('One of these entities no longer exists.')).toBeTruthy()
  cleanup()

  vi.mocked(api.edgeEvidence).mockRejectedValue(new ApiError('x', 409, { code: 'search_upgrade_required' }))
  renderPanel()
  expect(await screen.findByText('Search upgrade required. Rebuild the search index to use the entity graph.')).toBeTruthy()
  cleanup()

  vi.mocked(api.edgeEvidence).mockRejectedValue(new ApiError('down', 503))
  renderPanel()
  expect(await screen.findByText('Relationship evidence is temporarily unavailable.')).toBeTruthy()
  cleanup()

  vi.mocked(api.edgeEvidence).mockRejectedValue(new Error('boom'))
  renderPanel()
  expect(await screen.findByText('Could not load relationship evidence.')).toBeTruthy()
})
