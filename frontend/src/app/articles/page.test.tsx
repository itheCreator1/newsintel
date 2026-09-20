import { cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '../../lib/api'
import { clusterHref, toHref } from '../../lib/investigation'
import { renderWithQuery } from '../../test/render'
import { navigationHarness, resetNavigationHarness } from '../../test/navigation-harness'
import ArticlesPage from './page'

vi.mock('../../lib/api', () => ({
  api: {
    feeds: vi.fn(),
    articles: vi.fn(),
    article: vi.fn(),
    articleAnnotations: vi.fn(),
    processArticle: vi.fn(),
    reprocessArticle: vi.fn(),
  },
}))

const article = (id: string, title: string) => ({
  id,
  title,
  original_url: `https://example.com/${id}`,
  normalized_url: `https://example.com/${id}`,
  published_at: null,
  first_discovered_at: '2026-09-13T12:00:00Z',
  provenance: [],
})

beforeEach(() => {
  vi.clearAllMocks()
  resetNavigationHarness({ pathname: '/articles/' })
  vi.mocked(api.feeds).mockResolvedValue({ items: [], next_cursor: null })
  vi.mocked(api.articles)
    .mockResolvedValueOnce({ items: [article('one', 'First article')], next_cursor: 'next' })
    .mockResolvedValueOnce({ items: [article('two', 'Second article')], next_cursor: null })
  vi.mocked(api.articleAnnotations).mockResolvedValue({ article_id: 'one', capabilities: [], countries: [], entities: [], keywords: [], language: null, processors: [], source_countries: [] })
})

afterEach(cleanup)

it('loads the next cursor page without replacing earlier articles', async () => {
  renderWithQuery(() => <ArticlesPage />)

  expect(await screen.findByText('First article')).toBeTruthy()
  await fireEvent.click(screen.getByRole('button', { name: 'Load more articles' }))

  expect(await screen.findByText('Second article')).toBeTruthy()
  expect(screen.getByText('First article')).toBeTruthy()
  expect(api.articles).toHaveBeenLastCalledWith(undefined, 'next')
})

it('disables processing actions while a request is pending', async () => {
  resetNavigationHarness({ pathname: '/articles/', search: 'article=one' })
  vi.mocked(api.articles).mockReset().mockResolvedValue({ items: [article('one', 'First article')], next_cursor: null })
  vi.mocked(api.article).mockResolvedValue({ ...article('one', 'First article'), content: null, processing: [] })
  vi.mocked(api.processArticle).mockReturnValue(new Promise(() => {}))
  renderWithQuery(() => <ArticlesPage />)

  const button = await screen.findByRole('button', { name: 'Fetch text' })
  await fireEvent.click(button)

  await vi.waitFor(() => expect(button.hasAttribute('disabled')).toBe(true))
  expect(screen.getByText('Scheduling processing…')).toBeTruthy()
})

it('loads sources beyond the first page into the filter', async () => {
  vi.mocked(api.feeds)
    .mockReset()
    .mockResolvedValueOnce({ items: [{ id: 'feed-one', name: 'First source' } as never], next_cursor: 'feed-next' })
    .mockResolvedValueOnce({ items: [{ id: 'feed-two', name: 'Second source' } as never], next_cursor: null })
  renderWithQuery(() => <ArticlesPage />)

  await fireEvent.click(await screen.findByRole('button', { name: 'Load more sources' }))

  expect(await screen.findByRole('option', { name: 'Second source' })).toBeTruthy()
  expect(api.feeds).toHaveBeenLastCalledWith('feed-next')
})

it('starts article pagination over when the source filter changes', async () => {
  vi.mocked(api.feeds).mockResolvedValue({ items: [{ id: 'feed-one', name: 'First source' } as never], next_cursor: null })
  vi.mocked(api.articles).mockReset().mockImplementation(async (feedId, cursor) => {
    if (feedId === 'feed-one') return { items: [article('filtered', 'Filtered article')], next_cursor: null }
    if (cursor === 'next') return { items: [article('two', 'Second article')], next_cursor: null }
    return { items: [article('one', 'First article')], next_cursor: 'next' }
  })
  const { rerenderSame } = renderWithQuery(() => <ArticlesPage />)
  await fireEvent.click(await screen.findByRole('button', { name: 'Load more articles' }))
  fireEvent.change(screen.getByRole('combobox', { name: 'Source' }), { target: { value: 'feed-one' } })
  rerenderSame()

  await vi.waitFor(() => expect(api.articles).toHaveBeenLastCalledWith('feed-one', undefined))
})

it('shows scheduling success and clears it when another article is selected', async () => {
  resetNavigationHarness({ pathname: '/articles/', search: 'article=one' })
  vi.mocked(api.articles).mockReset().mockResolvedValue({ items: [article('one', 'First article'), article('two', 'Second article')], next_cursor: null })
  vi.mocked(api.article).mockImplementation(async id => ({ ...article(id, `${id} detail`), content: null, processing: [] }))
  vi.mocked(api.processArticle).mockResolvedValue({ job_id: 'job', status: 'queued', reused: false })
  const { rerenderSame } = renderWithQuery(() => <ArticlesPage />)

  await fireEvent.click(await screen.findByRole('button', { name: 'Fetch text' }))
  expect(await screen.findByText('Processing scheduled.')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: /Second article/ }))
  rerenderSame()

  await vi.waitFor(() => expect(screen.queryByText('Processing scheduled.')).toBeNull())
})

it('shows annotation meanings and preserves search criteria when refining', async () => {
  resetNavigationHarness({ pathname: '/articles/', search: 'article=one&from=%2Fsearch%3Fq%3Denergy%26country%3DUS' })
  vi.mocked(api.articles).mockReset().mockResolvedValue({ items: [article('one', 'First article')], next_cursor: null })
  vi.mocked(api.article).mockResolvedValue({ ...article('one', 'First article'), content: null, processing: [] })
  vi.mocked(api.articleAnnotations).mockResolvedValue({
    article_id: 'one', capabilities: [{ name: 'entities', state: 'disabled', detail: 'NER is disabled', version: null }],
    language: { language: 'en', confidence: 0.97, margin: 0.43, fresh: true }, source_countries: ['FR'],
    keywords: [{ id: 'keyword-one', text: 'climate policy', normalized_text: 'climate policy', kind: 'keyphrase', relevance: 0.9, raw_score: 0.1, occurrence_count: 2, fresh: true, occurrences: [] }],
    entities: [{ id: 'entity-one', text: 'Acme', normalized_text: 'acme', entity_type: 'ORG', original_label: 'ORG', relevance: 0.8, occurrence_count: 1, occurrences: [], fresh: true }],
    countries: [{ country_code: 'DE', role: 'mentioned', inferred: false, occurrence_count: 2, occurrences: [], fresh: true, rule_version: 'iso-country-lexicon-1' }, { country_code: 'DE', role: 'primary', inferred: true, occurrence_count: 2, occurrences: [], fresh: true, rule_version: 'primary-title-body-1' }],
    processors: [{ processor: 'keywords', status: 'queued', requested_generation: 2, completed_generation: 1, processor_version: '1', algorithm_version: 'yake', model_version: null, configuration_fingerprint: 'config', input_fingerprint: 'input', completed_at: '2026-09-14T12:00:00Z', detail: null }],
  })
  renderWithQuery(() => <ArticlesPage />)

  expect(await screen.findByText('Detected language: en')).toBeTruthy()
  expect(screen.getByText((_, node) => node?.tagName === 'P' && node.textContent === 'Source countries: FR')).toBeTruthy()
  expect(screen.getByText((_, node) => node?.tagName === 'P' && node.textContent === 'Primary story country: DE (inferred)')).toBeTruthy()
  expect(screen.getByText(/keywords · stale · queued/)).toBeTruthy()
  expect(screen.getByText((_, node) => node?.textContent === 'entities · disabled · NER is disabled')).toBeTruthy()
  expect(screen.getByRole('link', { name: 'climate policy' })).toHaveAttribute('href', '/search/?q=energy&country=US&keyword_id=keyword-one')
  expect(screen.getByRole('link', { name: 'Acme (ORG)' })).toHaveAttribute('href', '/search/?q=energy&country=US&entity_id=entity-one')
  expect(screen.getByRole('link', { name: 'Open dossier for Acme' })).toHaveAttribute('href', '/entities/?id=entity-one')
})

it('shows the not-part-of-a-story empty state when an article has no cluster', async () => {
  resetNavigationHarness({ pathname: '/articles/', search: 'article=one' })
  vi.mocked(api.articles).mockReset().mockResolvedValue({ items: [article('one', 'First article')], next_cursor: null })
  vi.mocked(api.article).mockResolvedValue({ ...article('one', 'First article'), content: null, processing: [] })
  renderWithQuery(() => <ArticlesPage />)

  expect(await screen.findByText('Not part of a detected story.')).toBeTruthy()
})

it('shows the story section with related articles and links to the full cluster and a story filter', async () => {
  const search = 'article=one&from=%2Fsearch%3Fq%3Denergy%26country%3DUS'
  resetNavigationHarness({ pathname: '/articles/', search })
  vi.mocked(api.articles).mockReset().mockResolvedValue({ items: [article('one', 'First article')], next_cursor: null })
  vi.mocked(api.article).mockResolvedValue({
    ...article('one', 'First article'), content: null, processing: [],
    story_cluster: { id: 'cluster-1', article_count: 3, source_count: 2 },
    related: [{ article_id: 'two', title: 'Second report', effective_date: '2026-09-13T13:00:00Z', score: 0.8 }],
  })
  renderWithQuery(() => <ArticlesPage />)

  expect(await screen.findByText('3 articles from 2 sources')).toBeTruthy()
  expect(screen.getByRole('link', { name: 'Second report' })).toBeTruthy()
  const currentHref = toHref('/articles/', navigationHarness.searchParams)
  expect(screen.getByRole('link', { name: 'Open full cluster' })).toHaveAttribute('href', clusterHref('cluster-1', currentHref))
  expect(screen.getByRole('link', { name: 'Filter by story' })).toHaveAttribute('href', '/search/?q=energy&country=US&story_cluster_id=cluster-1')
})

it.each([
  ['Filter by source Wire', '/search/?q=energy&source_id=feed-wire&country=US&after=2026-01-01'],
  ['Filter by source country FR', '/search/?q=energy&country=FR&after=2026-01-01'],
  ['Filter by mentioned country DE', '/search/?q=energy&country=US&mentioned_country=DE&after=2026-01-01'],
  ['Filter by story country GR', '/search/?q=energy&country=US&story_country=GR&after=2026-01-01'],
])('cross-filters the originating investigation from %s', async (name, expectedHref) => {
  resetNavigationHarness({ pathname: '/articles/', search: 'article=one&from=%2Fsearch%3Fq%3Denergy%26country%3DUS%26after%3D2026-01-01' })
  vi.mocked(api.articles).mockReset().mockResolvedValue({ items: [article('one', 'First article')], next_cursor: null })
  vi.mocked(api.article).mockResolvedValue({ ...article('one', 'First article'), provenance: [{ feed_id: 'feed-wire', feed_name: 'Wire', description: null, discovered_at: '2026-09-13T12:00:00Z', guid: null, title: 'First article', url: 'https://example.com/one' }], content: null, processing: [] })
  vi.mocked(api.articleAnnotations).mockResolvedValue({
    article_id: 'one', capabilities: [], language: null, source_countries: ['FR'], keywords: [], entities: [], processors: [],
    countries: [{ country_code: 'DE', role: 'mentioned', inferred: false, occurrence_count: 1, occurrences: [], fresh: true, rule_version: 'r' }, { country_code: 'GR', role: 'primary', inferred: false, occurrence_count: 1, occurrences: [], fresh: true, rule_version: 'r' }],
  })
  renderWithQuery(() => <ArticlesPage />)

  expect(await screen.findByRole('link', { name })).toHaveAttribute('href', expectedHref)
})
