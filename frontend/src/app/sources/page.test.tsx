import { cleanup, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '../../lib/api'
import { renderWithQuery } from '../../test/render'
import SourcesPage from './page'

vi.mock('../../lib/api', () => ({ api: { feeds: vi.fn(), fetches: vi.fn(), createFeed: vi.fn(), updateFeed: vi.fn(), pollFeed: vi.fn(), retireFeed: vi.fn() } }))

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.feeds).mockResolvedValue({
    items: [{ id: 'f1', name: 'Wire', url: 'https://wire.example/rss', source_country: 'GR', expected_language: null, tags: [], enabled: true, poll_interval_minutes: 30, fetching_mode: 'rss', next_poll_at: '2026-09-21T10:30:00Z', last_success_at: '2026-09-21T10:00:00Z', created_at: '2026-08-01T00:00:00Z' }],
    next_cursor: null,
  })
})
afterEach(cleanup)

it('links each managed source to its dossier', async () => {
  renderWithQuery(() => <SourcesPage />)

  expect((await screen.findByRole('link', { name: 'Dossier for Wire' })).getAttribute('href')).toBe('/sources/detail/?id=f1')
})
