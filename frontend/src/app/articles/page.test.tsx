import { cleanup, fireEvent, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '../../lib/api'
import { clusterHref, toHref } from '../../lib/investigation'
import { renderWithQuery } from '../../test/render'
import { navigationHarness, resetNavigationHarness } from '../../test/navigation-harness'
import ArticlesPage from './page'

vi.mock('../../lib/api', async importOriginal => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: {
    feeds: vi.fn(),
    articles: vi.fn(),
    article: vi.fn(),
    articleAnnotations: vi.fn(),
    relatedArticles: vi.fn(),
    processArticle: vi.fn(),
    reprocessArticle: vi.fn(),
    seeAlso: vi.fn(),
    addRelation: vi.fn(),
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
  localStorage.clear()
  resetNavigationHarness({ pathname: '/articles/' })
  vi.mocked(api.feeds).mockResolvedValue({ items: [], next_cursor: null })
  vi.mocked(api.articles)
    .mockResolvedValueOnce({ items: [article('one', 'First article')], next_cursor: 'next' })
    .mockResolvedValueOnce({ items: [article('two', 'Second article')], next_cursor: null })
  vi.mocked(api.articleAnnotations).mockResolvedValue({ article_id: 'one', capabilities: [], countries: [], entities: [], keywords: [], language: null, processors: [], source_countries: [] })
  vi.mocked(api.relatedArticles).mockResolvedValue({ items: [], skipped_stale: 0 })
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

it('labels the way back to an event dossier', async () => {
  resetNavigationHarness({ pathname: '/articles/', search: 'article=one&from=%2Fevents%2Fdetail%2F%3Fid%3Dev-1' })
  vi.mocked(api.articles).mockReset().mockResolvedValue({ items: [article('one', 'First article')], next_cursor: null })
  vi.mocked(api.article).mockResolvedValue({ ...article('one', 'First article'), content: null, processing: [] })
  renderWithQuery(() => <ArticlesPage />)

  expect(await screen.findByRole('link', { name: 'Back to event' })).toHaveAttribute('href', '/events/detail/?id=ev-1')
})

it('labels the way back to a source dossier and links each provenance source to its dossier', async () => {
  resetNavigationHarness({ pathname: '/articles/', search: 'article=one&from=%2Fsources%2Fdetail%2F%3Fid%3Dfeed-wire' })
  vi.mocked(api.articles).mockReset().mockResolvedValue({ items: [article('one', 'First article')], next_cursor: null })
  vi.mocked(api.article).mockResolvedValue({ ...article('one', 'First article'), provenance: [{ feed_id: 'feed-wire', feed_name: 'Wire', description: null, discovered_at: '2026-09-13T12:00:00Z', guid: null, title: 'First article', url: 'https://example.com/one' }], content: null, processing: [] })
  renderWithQuery(() => <ArticlesPage />)

  expect(await screen.findByRole('link', { name: 'Back to source' })).toHaveAttribute('href', '/sources/detail/?id=feed-wire')
  expect(screen.getByRole('link', { name: 'Dossier for source Wire' }).getAttribute('href')).toMatch(/^\/sources\/detail\/\?id=feed-wire&from=%2Farticles%2F/)
})

it('labels the way back to a comparison', async () => {
  resetNavigationHarness({ pathname: '/articles/', search: 'article=one&from=%2Fcompare%2F%3Fkind%3Dentity%26a%3De1%26b%3De2' })
  vi.mocked(api.articles).mockReset().mockResolvedValue({ items: [article('one', 'First article')], next_cursor: null })
  vi.mocked(api.article).mockResolvedValue({ ...article('one', 'First article'), provenance: [], content: null, processing: [] })
  renderWithQuery(() => <ArticlesPage />)

  expect(await screen.findByRole('link', { name: 'Back to comparison' })).toHaveAttribute('href', '/compare/?kind=entity&a=e1&b=e2')
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

it('shows related coverage apart from the story and opens a related article in place', async () => {
  resetNavigationHarness({ pathname: '/articles/', search: 'article=one&feed=f1' })
  vi.mocked(api.articles).mockReset().mockResolvedValue({ items: [article('one', 'First article')], next_cursor: null })
  vi.mocked(api.article).mockResolvedValue({ ...article('one', 'First article'), content: null, processing: [] })
  vi.mocked(api.relatedArticles).mockResolvedValue({ items: [{ article: article('two', 'Worded alike'), score: 2 }], skipped_stale: 0 })
  renderWithQuery(() => <ArticlesPage />)

  const panel = await screen.findByRole('region', { name: 'Related coverage' })
  expect(await screen.findByRole('link', { name: 'Worded alike' })).toHaveAttribute('href', toHref('/articles/', new URLSearchParams('article=two&feed=f1')))
  expect(panel.textContent).toMatch(/not a confirmed connection/)
  expect(api.relatedArticles).toHaveBeenCalledWith('one')
})

const content = (text: string) => ({ text, content_hash: 'h', previous_content_hash: null, change_count: 0, extractor_name: 'x', extractor_version: '1', extracted_at: '2026-09-13T12:00:00Z', last_content_change_at: '2026-09-13T12:00:00Z', html_retained: false })

function readingList() {
  vi.mocked(api.articles).mockReset().mockResolvedValue({ items: [article('one', 'First article'), article('two', 'Second article')], next_cursor: null })
  vi.mocked(api.article).mockImplementation(async id => ({ ...article(id, `${id} detail`), content: null, processing: [] }))
}

it('marks the open article in the list and steps through it with j and k', async () => {
  resetNavigationHarness({ pathname: '/articles/', search: 'article=one' })
  readingList()
  const { rerenderSame } = renderWithQuery(() => <ArticlesPage />)

  expect((await screen.findByRole('button', { name: /First article/ })).getAttribute('aria-current')).toBe('true')
  expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
  fireEvent.keyDown(document.body, { key: 'j' })
  expect(navigationHarness.searchParams.get('article')).toBe('two')
  rerenderSame()

  expect(screen.getByRole('button', { name: /Second article/ }).getAttribute('aria-current')).toBe('true')
  expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
  fireEvent.keyDown(document.body, { key: 'k' })
  expect(navigationHarness.searchParams.get('article')).toBe('one')
})

it('loads the next page when stepping past the last loaded article', async () => {
  resetNavigationHarness({ pathname: '/articles/', search: 'article=one' })
  vi.mocked(api.article).mockImplementation(async id => ({ ...article(id, `${id} detail`), content: null, processing: [] }))
  renderWithQuery(() => <ArticlesPage />)

  await fireEvent.click(await screen.findByRole('button', { name: 'Next' }))

  await vi.waitFor(() => expect(navigationHarness.searchParams.get('article')).toBe('two'))
  expect(api.articles).toHaveBeenLastCalledWith(undefined, 'next')
})

it('ignores reading shortcuts typed into a field or with a modifier held', async () => {
  resetNavigationHarness({ pathname: '/articles/', search: 'article=one' })
  readingList()
  renderWithQuery(() => <ArticlesPage />)
  await screen.findByRole('button', { name: /First article/ })

  fireEvent.keyDown(screen.getByRole('combobox', { name: 'Source' }), { key: 'j' })
  fireEvent.keyDown(document.body, { key: 'j', ctrlKey: true })
  fireEvent.keyDown(document.body, { key: 'f', metaKey: true })

  expect(navigationHarness.replace).not.toHaveBeenCalled()
})

it('enters focus mode with f, hides the list, and leaves it with Escape', async () => {
  resetNavigationHarness({ pathname: '/articles/', search: 'article=one' })
  readingList()
  const { rerenderSame } = renderWithQuery(() => <ArticlesPage />)
  await screen.findByRole('button', { name: /First article/ })

  fireEvent.keyDown(document.body, { key: 'f' })
  expect(navigationHarness.searchParams.get('focus')).toBe('1')
  rerenderSame()
  expect(screen.queryByRole('button', { name: /First article/ })).toBeNull()
  expect(screen.getByRole('button', { name: 'Exit focus' })).toBeTruthy()
  expect(await screen.findByRole('heading', { level: 3, name: 'one detail' })).toBeTruthy()

  fireEvent.keyDown(document.body, { key: 'Escape' })
  expect(navigationHarness.searchParams.get('focus')).toBeNull()
})

it('highlights annotated words in the text and lets highlights be switched off', async () => {
  resetNavigationHarness({ pathname: '/articles/', search: 'article=one&from=%2Fsearch%3Fq%3Denergy' })
  vi.mocked(api.articles).mockReset().mockResolvedValue({ items: [article('one', 'First article')], next_cursor: null })
  vi.mocked(api.article).mockResolvedValue({ ...article('one', 'First article'), content: content('Acme signs\n\na climate policy deal.'), processing: [] })
  const occurrence = (start: number, end: number) => ({ section: 'body', reference_id: null, start, end, input_start: start, input_end: end })
  vi.mocked(api.articleAnnotations).mockResolvedValue({
    article_id: 'one', capabilities: [], countries: [], language: null, processors: [], source_countries: [],
    // Offsets index "Acme signs a climate policy deal.", the whitespace-collapsed text.
    keywords: [{ id: 'keyword-one', text: 'climate policy', normalized_text: 'climate policy', kind: 'keyphrase', relevance: 0.9, raw_score: 0.1, occurrence_count: 1, fresh: true, occurrences: [occurrence(13, 27)] }],
    entities: [{ id: 'entity-one', text: 'Acme', normalized_text: 'acme', entity_type: 'ORG', original_label: 'ORG', relevance: 0.8, occurrence_count: 1, fresh: true, occurrences: [occurrence(0, 4)] }],
  })
  const { container } = renderWithQuery(() => <ArticlesPage />)

  await vi.waitFor(() => expect(container.querySelectorAll('.reader-newsprint mark')).toHaveLength(2))
  const [entity, keyword] = [...container.querySelectorAll('.reader-newsprint mark a')]
  expect(entity.textContent).toBe('Acme')
  expect(entity).toHaveAttribute('href', '/entities/?id=entity-one')
  expect(keyword.textContent).toBe('climate policy')
  expect(keyword).toHaveAttribute('href', '/search/?q=energy&keyword_id=keyword-one')

  fireEvent.click(screen.getByRole('button', { name: 'Highlights' }))
  expect(container.querySelector('.reader-newsprint mark')).toBeNull()
  expect(screen.getByRole('button', { name: 'Highlights' }).getAttribute('aria-pressed')).toBe('false')
})

it('links two of the article’s entities with the article as the source', async () => {
  resetNavigationHarness({ pathname: '/articles/', search: 'article=one' })
  vi.mocked(api.articles).mockReset().mockResolvedValue({ items: [article('one', 'First article')], next_cursor: null })
  vi.mocked(api.article).mockResolvedValue({ ...article('one', 'First article'), content: null, processing: [] })
  const entity = (id: string, text: string, entity_type: string) => ({ id, text, normalized_text: text.toLowerCase(), entity_type, original_label: entity_type, relevance: 0.8, occurrence_count: 1, occurrences: [], fresh: true })
  vi.mocked(api.articleAnnotations).mockResolvedValue({
    article_id: 'one', capabilities: [], countries: [], keywords: [], language: null, processors: [], source_countries: [],
    entities: [entity('entity-one', 'Facebook', 'ORG'), entity('entity-two', 'Meta', 'ORG'), entity('entity-three', 'Mark Zuckerberg', 'PERSON')],
  })
  vi.mocked(api.seeAlso).mockResolvedValue({ labels: ['later_name', 'earlier_name', 'part_of', 'has_part', 'related'], items: [] })
  vi.mocked(api.addRelation).mockResolvedValue({} as never)
  renderWithQuery(() => <ArticlesPage />)

  fireEvent.click(await screen.findByRole('button', { name: 'Link two entities' }))
  const form = screen.getByRole('form', { name: 'Link entities from this article' })
  fireEvent.change(within(form).getByLabelText('Entity'), { target: { value: 'entity-one' } })
  await vi.waitFor(() => expect(api.seeAlso).toHaveBeenCalledWith('entity-one'))
  fireEvent.change(await within(form).findByLabelText('Link type'), { target: { value: 'later_name' } })
  const linked = within(form).getByLabelText('Linked entity')
  expect([...linked.querySelectorAll('option')].map(option => option.textContent)).toEqual(['Choose an entity', 'Meta (ORG)', 'Mark Zuckerberg (PERSON)'])
  fireEvent.change(linked, { target: { value: 'entity-two' } })
  fireEvent.click(within(form).getByRole('button', { name: 'Save link' }))

  await vi.waitFor(() => expect(api.addRelation).toHaveBeenCalledWith('entity-one', { label: 'later_name', target_id: 'entity-two', source_article_id: 'one' }))
  expect(await screen.findByRole('status')).toHaveTextContent('Linked Facebook to Meta, with this article as the source.')
})

it('offers no entity link with fewer than two entities', async () => {
  resetNavigationHarness({ pathname: '/articles/', search: 'article=one' })
  vi.mocked(api.articles).mockReset().mockResolvedValue({ items: [article('one', 'First article')], next_cursor: null })
  vi.mocked(api.article).mockResolvedValue({ ...article('one', 'First article'), content: null, processing: [] })
  vi.mocked(api.articleAnnotations).mockResolvedValue({
    article_id: 'one', capabilities: [], countries: [], keywords: [], language: null, processors: [], source_countries: [],
    entities: [{ id: 'entity-one', text: 'Acme', normalized_text: 'acme', entity_type: 'ORG', original_label: 'ORG', relevance: 0.8, occurrence_count: 1, occurrences: [], fresh: true }],
  })
  renderWithQuery(() => <ArticlesPage />)

  expect(await screen.findByRole('link', { name: 'Open dossier for Acme' })).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Link two entities' })).toBeNull()
})

it('sends a note with the link, which a related link needs', async () => {
  resetNavigationHarness({ pathname: '/articles/', search: 'article=one' })
  vi.mocked(api.articles).mockReset().mockResolvedValue({ items: [article('one', 'First article')], next_cursor: null })
  vi.mocked(api.article).mockResolvedValue({ ...article('one', 'First article'), content: null, processing: [] })
  const entity = (id: string, text: string, entity_type: string) => ({ id, text, normalized_text: text.toLowerCase(), entity_type, original_label: entity_type, relevance: 0.8, occurrence_count: 1, occurrences: [], fresh: true })
  vi.mocked(api.articleAnnotations).mockResolvedValue({
    article_id: 'one', capabilities: [], countries: [], keywords: [], language: null, processors: [], source_countries: [],
    entities: [entity('entity-one', 'United Nations', 'ORG'), entity('entity-two', 'Brussels', 'GPE')],
  })
  vi.mocked(api.seeAlso).mockResolvedValue({ labels: ['related'], items: [] })
  vi.mocked(api.addRelation).mockResolvedValue({} as never)
  renderWithQuery(() => <ArticlesPage />)

  fireEvent.click(await screen.findByRole('button', { name: 'Link two entities' }))
  const form = screen.getByRole('form', { name: 'Link entities from this article' })
  fireEvent.change(within(form).getByRole('combobox', { name: 'Entity' }), { target: { value: 'entity-one' } })
  await within(form).findByRole('combobox', { name: 'Link type' })
  fireEvent.change(within(form).getByRole('combobox', { name: 'Linked entity' }), { target: { value: 'entity-two' } })
  fireEvent.change(within(form).getByRole('textbox', { name: 'Link note' }), { target: { value: '  Host city  ' } })
  fireEvent.click(within(form).getByRole('button', { name: 'Save link' }))

  await vi.waitFor(() => expect(api.addRelation).toHaveBeenCalledWith('entity-one', { label: 'related', target_id: 'entity-two', source_article_id: 'one', note: 'Host city' }))
})
