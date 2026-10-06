import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../../lib/api'
import { renderWithQuery } from '../../test/render'
import SourcesPage from './page'

vi.mock('../../lib/api', async importOriginal => ({ ...(await importOriginal<typeof import('../../lib/api')>()), api: { feeds: vi.fn(), fetches: vi.fn(), createFeed: vi.fn(), updateFeed: vi.fn(), pollFeed: vi.fn(), retireFeed: vi.fn() } }))

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

it('saves a valid poll interval on blur and ignores an out-of-range or empty one', async () => {
  renderWithQuery(() => <SourcesPage />)
  const input = await screen.findByLabelText('Poll interval for Wire') as HTMLInputElement

  for (const value of ['2', '', '60']) {
    fireEvent.change(input, { target: { value } })
    fireEvent.blur(input)
  }
  await waitFor(() => expect(api.updateFeed).toHaveBeenCalled())
  expect(vi.mocked(api.updateFeed).mock.calls).toEqual([['f1', { poll_interval_minutes: 60 }]])
})

it('tells a failed load apart from an empty list and offers Retry', async () => {
  vi.mocked(api.feeds).mockRejectedValueOnce(new ApiError('Request failed', 503))
  renderWithQuery(() => <SourcesPage />)

  expect(await screen.findByRole('alert')).toHaveTextContent('Could not load sources.')
  expect(screen.queryByText(/No sources yet/)).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  expect(await screen.findByRole('link', { name: 'Dossier for Wire' })).toBeTruthy()
})

it('points an empty archive at the add form', async () => {
  vi.mocked(api.feeds).mockResolvedValue({ items: [], next_cursor: null })
  renderWithQuery(() => <SourcesPage />)

  expect(await screen.findByText('No sources yet. Add an RSS or Atom feed with the form to start collecting.')).toBeTruthy()
})

it('says why a source could not be added', async () => {
  vi.mocked(api.createFeed).mockRejectedValue(new ApiError('url: Feed URL must use http or https', 422))
  renderWithQuery(() => <SourcesPage />)
  fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'Wire two' } })
  fireEvent.change(screen.getByLabelText('Feed URL'), { target: { value: 'https://two.example/rss' } })
  fireEvent.click(screen.getByRole('button', { name: 'Add source' }))

  expect(await screen.findByRole('alert')).toHaveTextContent('url: Feed URL must use http or https')
})
