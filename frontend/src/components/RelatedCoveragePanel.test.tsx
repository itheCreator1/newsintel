import { cleanup, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../lib/api'
import { renderWithQuery } from '../test/render'
import { RelatedCoveragePanel } from './RelatedCoveragePanel'

vi.mock('../lib/api', async importOriginal => ({ ...(await importOriginal<typeof import('../lib/api')>()), api: { relatedArticles: vi.fn() } }))

const article = (id: string, title: string, feed = 'Harbor Wire') => ({ id, title, original_url: `https://x/${id}`, normalized_url: `https://x/${id}`, published_at: null, first_discovered_at: '2026-09-14T13:00:00Z', provenance: [{ feed_id: 'f1', feed_name: feed, guid: null, title, url: `https://x/${id}`, description: null, discovered_at: '2026-09-14T13:00:00Z' }] })
const renderPanel = () => renderWithQuery(() => <RelatedCoveragePanel articleId="a1" hrefFor={id => `/articles/?article=${id}`} />)

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.relatedArticles).mockResolvedValue({ items: [{ article: article('a2', 'Port delays spread'), score: 3.2 }, { article: article('a3', 'Shipping lines wait', 'Harbor Daily'), score: 2.1 }], skipped_stale: 0 })
})
afterEach(cleanup)

it('lists similar coverage and says it is not the story', async () => {
  renderPanel()

  const panel = screen.getByRole('region', { name: 'Related coverage' })
  expect(panel.textContent).toMatch(/similar wording, outside this article's story/i)
  expect(panel.textContent).toMatch(/not a confirmed connection/i)
  expect(await screen.findByRole('link', { name: 'Port delays spread' })).toHaveAttribute('href', '/articles/?article=a2')
  expect(screen.getByRole('link', { name: 'Shipping lines wait' })).toBeTruthy()
  expect(screen.getByText('Harbor Daily')).toBeTruthy()
  expect(api.relatedArticles).toHaveBeenCalledWith('a1')
})

it('says when nothing is worded alike and when the index was behind', async () => {
  vi.mocked(api.relatedArticles).mockResolvedValue({ items: [], skipped_stale: 2 })
  renderPanel()

  expect(await screen.findByText('No other coverage with similar wording.')).toBeTruthy()
  expect(screen.getByText('2 matches were skipped because the search index is catching up.')).toBeTruthy()
})

it('shows the upgrade message for an old index and a plain message when search is down', async () => {
  vi.mocked(api.relatedArticles).mockRejectedValueOnce(new ApiError('Rebuild search to schema version 3 to use this view', 409))
  renderPanel()
  expect(await screen.findByText('Rebuild search to schema version 3 to use this view')).toBeTruthy()
  cleanup()

  vi.mocked(api.relatedArticles).mockRejectedValueOnce(new ApiError('Related coverage is unavailable', 503))
  renderPanel()
  expect(await screen.findByText('Related coverage is unavailable right now.')).toBeTruthy()
})
