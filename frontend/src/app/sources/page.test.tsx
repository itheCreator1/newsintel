import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../../lib/api'
import { renderWithQuery } from '../../test/render'
import SourcesPage from './page'

vi.mock('../../lib/api', async importOriginal => ({ ...(await importOriginal<typeof import('../../lib/api')>()), api: { feeds: vi.fn(), feedTags: vi.fn(), testFeed: vi.fn(), previewOpml: vi.fn(), importFeeds: vi.fn(), fetches: vi.fn(), createFeed: vi.fn(), updateFeed: vi.fn(), pollFeed: vi.fn(), retireFeed: vi.fn() } }))

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.feeds).mockResolvedValue({
    items: [{ id: 'f1', name: 'Wire', url: 'https://wire.example/rss', source_country: 'GR', expected_language: null, tags: [], enabled: true, poll_interval_minutes: 30, fetching_mode: 'rss', next_poll_at: '2026-09-21T10:30:00Z', last_success_at: '2026-09-21T10:00:00Z', created_at: '2026-08-01T00:00:00Z' }],
    next_cursor: null,
  })
  vi.mocked(api.feedTags).mockResolvedValue([{ tag: 'greek', count: 2 }, { tag: 'economy', count: 1 }])
})

const wire = { id: 'f1', name: 'Wire', url: 'https://wire.example/rss', source_country: 'GR', expected_language: null, tags: ['greek'], enabled: true, poll_interval_minutes: 30, fetching_mode: 'rss' as const, next_poll_at: '2026-09-21T10:30:00Z', last_success_at: '2026-09-21T10:00:00Z', created_at: '2026-08-01T00:00:00Z', retired_at: null, last_fetch_status: 'success', last_fetch_error: null, last_fetch_at: '2026-09-21T10:00:00Z' }
afterEach(cleanup)

it('links each managed source to its dossier', async () => {
  renderWithQuery(() => <SourcesPage />)

  expect((await screen.findByRole('link', { name: 'Dossier for Wire' })).getAttribute('href')).toBe('/sources/detail/?id=f1')
})

it('edits a source and sends only what changed', async () => {
  vi.mocked(api.feeds).mockResolvedValue({ items: [wire], next_cursor: null })
  vi.mocked(api.updateFeed).mockResolvedValue({ ...wire, name: 'Wire GR' })
  renderWithQuery(() => <SourcesPage />)
  fireEvent.click(await screen.findByRole('button', { name: 'Edit Wire' }))
  const form = within(screen.getByRole('form', { name: 'Edit Wire' }))

  expect(form.getByLabelText('Name')).toHaveValue('Wire')
  expect(form.getByLabelText('Source country')).toHaveValue('GR')
  expect(form.getByLabelText('Tags')).toHaveValue('greek')
  fireEvent.change(form.getByLabelText('Name'), { target: { value: 'Wire GR' } })
  fireEvent.change(form.getByLabelText('Expected language'), { target: { value: 'el' } })
  fireEvent.change(form.getByLabelText('Tags'), { target: { value: 'greek, economy' } })
  fireEvent.change(form.getByLabelText('Poll interval (minutes)'), { target: { value: '15' } })
  fireEvent.click(form.getByRole('button', { name: 'Save changes' }))

  await waitFor(() => expect(api.updateFeed).toHaveBeenCalledWith('f1', { name: 'Wire GR', expected_language: 'el', tags: ['greek', 'economy'], poll_interval_minutes: 15 }))
  await waitFor(() => expect(screen.queryByRole('form', { name: 'Edit Wire' })).toBeNull())
})

it('clears a country, turns collection off, and says why a change was refused', async () => {
  vi.mocked(api.feeds).mockResolvedValue({ items: [wire], next_cursor: null })
  vi.mocked(api.updateFeed).mockRejectedValue(new ApiError('url: Feed URL must use http or https', 422))
  renderWithQuery(() => <SourcesPage />)
  fireEvent.click(await screen.findByRole('button', { name: 'Edit Wire' }))
  const form = within(screen.getByRole('form', { name: 'Edit Wire' }))
  fireEvent.change(form.getByLabelText('Source country'), { target: { value: '' } })
  fireEvent.click(form.getByRole('checkbox', { name: 'Collect from this source' }))
  fireEvent.click(form.getByRole('button', { name: 'Save changes' }))

  await waitFor(() => expect(api.updateFeed).toHaveBeenCalledWith('f1', { source_country: null, enabled: false }))
  expect(await form.findByRole('alert')).toHaveTextContent('url: Feed URL must use http or https')
  expect(screen.getByRole('form', { name: 'Edit Wire' })).toBeTruthy()
})

it('retires a source from its edit form after asking', async () => {
  vi.mocked(api.feeds).mockResolvedValue({ items: [wire], next_cursor: null })
  vi.mocked(api.retireFeed).mockResolvedValue(undefined)
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
  renderWithQuery(() => <SourcesPage />)
  fireEvent.click(await screen.findByRole('button', { name: 'Edit Wire' }))
  fireEvent.click(within(screen.getByRole('form', { name: 'Edit Wire' })).getByRole('button', { name: 'Retire source' }))

  expect(confirm).toHaveBeenCalled()
  await waitFor(() => expect(api.retireFeed).toHaveBeenCalledWith('f1'))
  confirm.mockRestore()
})

it('finds, filters, sorts and pages the sources', async () => {
  vi.mocked(api.feeds).mockImplementation(async cursor => cursor
    ? { items: [{ ...wire, id: 'f2', name: 'Daily' }], next_cursor: null }
    : { items: [wire], next_cursor: 'page-2' })
  renderWithQuery(() => <SourcesPage />)
  await screen.findByRole('link', { name: 'Dossier for Wire' })

  fireEvent.change(screen.getByLabelText('Find a source'), { target: { value: 'wire' } })
  await waitFor(() => expect(api.feeds).toHaveBeenLastCalledWith(undefined, { q: 'wire' }))
  fireEvent.change(screen.getByLabelText('Show'), { target: { value: 'failing' } })
  await waitFor(() => expect(api.feeds).toHaveBeenLastCalledWith(undefined, { q: 'wire', status: 'failing' }))
  expect(await screen.findByRole('option', { name: 'greek (2)' })).toBeTruthy()
  fireEvent.change(screen.getByLabelText('Tag'), { target: { value: 'greek' } })
  fireEvent.change(screen.getByLabelText('Sort'), { target: { value: 'name' } })
  await waitFor(() => expect(api.feeds).toHaveBeenLastCalledWith(undefined, { q: 'wire', status: 'failing', tag: 'greek', sort: 'name' }))

  expect(screen.queryByRole('button', { name: 'Previous page' })).toBeNull()
  fireEvent.click(await screen.findByRole('button', { name: 'Next page' }))
  expect(await screen.findByRole('link', { name: 'Dossier for Daily' })).toBeTruthy()
  expect(api.feeds).toHaveBeenLastCalledWith('page-2', { q: 'wire', status: 'failing', tag: 'greek', sort: 'name' })
  fireEvent.click(screen.getByRole('button', { name: 'Previous page' }))
  expect(await screen.findByRole('link', { name: 'Dossier for Wire' })).toBeTruthy()

  // A new filter starts again from the first page.
  fireEvent.click(await screen.findByRole('button', { name: 'Next page' }))
  await screen.findByRole('link', { name: 'Dossier for Daily' })
  fireEvent.change(screen.getByLabelText('Sort'), { target: { value: 'newest' } })
  await waitFor(() => expect(api.feeds).toHaveBeenLastCalledWith(undefined, { q: 'wire', status: 'failing', tag: 'greek' }))
})

it('filters by a tag from a source row', async () => {
  vi.mocked(api.feeds).mockResolvedValue({ items: [wire], next_cursor: null })
  renderWithQuery(() => <SourcesPage />)
  fireEvent.click(await screen.findByRole('button', { name: 'Filter by tag greek' }))
  await waitFor(() => expect(api.feeds).toHaveBeenLastCalledWith(undefined, { tag: 'greek' }))
  expect(screen.getByLabelText('Tag')).toHaveValue('greek')
})

it('shows country, language and the last error, and a retired source without actions', async () => {
  vi.mocked(api.feeds).mockResolvedValue({ items: [
    { ...wire, expected_language: 'el', last_fetch_status: 'failed', last_fetch_error: 'http: HTTP 500' },
    { ...wire, id: 'f9', name: 'Old wire', enabled: false, retired_at: '2026-09-01T00:00:00Z' },
  ], next_cursor: null })
  renderWithQuery(() => <SourcesPage />)
  const row = (await screen.findByText('Wire')).closest('.source-row') as HTMLElement
  expect(within(row).getByText('Greece (GR) · Greek (el) · rss · every 30 min')).toBeTruthy()
  expect(within(row).getByText('Failing')).toBeTruthy()
  expect(within(row).getByText('Last fetch failed: http: HTTP 500')).toBeTruthy()

  const retired = screen.getByText('Old wire').closest('.source-row') as HTMLElement
  expect(within(retired).getByText('Retired')).toBeTruthy()
  expect(within(retired).queryByRole('button')).toBeNull()
})

it('leaves the fetch history to the dossier', async () => {
  renderWithQuery(() => <SourcesPage />)
  fireEvent.click(await screen.findByText('Wire'))
  expect(screen.queryByText(/fetch history/i)).toBeNull()
  expect(api.fetches).not.toHaveBeenCalled()
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

const probe = { url: 'https://news.example/rss.xml', title: 'Η Καθημερινή', entry_count: 20, language: 'el', latest_entry_at: '2026-10-12T08:00:00Z', discovered_from: 'https://news.example/', candidates: [{ url: 'https://news.example/rss.xml', title: 'All news' }], existing_feed_id: null }

it('tests a site address, finds its feed and fills in what it found', async () => {
  vi.mocked(api.testFeed).mockResolvedValue(probe)
  renderWithQuery(() => <SourcesPage />)
  fireEvent.change(await screen.findByLabelText('Feed URL'), { target: { value: 'https://news.example/' } })
  fireEvent.click(screen.getByRole('button', { name: 'Test feed' }))

  const result = await screen.findByRole('status', { name: 'Feed test' })
  expect(api.testFeed).toHaveBeenCalledWith('https://news.example/')
  expect(within(result).getByText('Η Καθημερινή · 20 entries · Greek (el)')).toBeTruthy()
  expect(within(result).getByText('Found the feed linked from https://news.example/')).toBeTruthy()
  fireEvent.click(within(result).getByRole('button', { name: 'Use these details' }))
  expect(screen.getByLabelText('Name')).toHaveValue('Η Καθημερινή')
  expect(screen.getByLabelText('Feed URL')).toHaveValue('https://news.example/rss.xml')
  expect(within(screen.getByRole('form', { name: 'Add source' })).getByLabelText('Expected language')).toHaveValue('el')
})

it('keeps a name you typed, and says when the feed is already a source', async () => {
  vi.mocked(api.testFeed).mockResolvedValue({ ...probe, discovered_from: null, existing_feed_id: 'f1' })
  renderWithQuery(() => <SourcesPage />)
  fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'My name' } })
  fireEvent.change(screen.getByLabelText('Feed URL'), { target: { value: 'https://news.example/rss.xml' } })
  fireEvent.click(screen.getByRole('button', { name: 'Test feed' }))

  const result = await screen.findByRole('status', { name: 'Feed test' })
  expect(within(result).getByText('This feed is already a source.')).toBeTruthy()
  expect(within(result).getByRole('link', { name: 'Open its dossier' })).toHaveAttribute('href', '/sources/detail/?id=f1')
  fireEvent.click(within(result).getByRole('button', { name: 'Use these details' }))
  expect(screen.getByLabelText('Name')).toHaveValue('My name')
})

it('says why a feed address did not work', async () => {
  vi.mocked(api.testFeed).mockRejectedValue(new ApiError('The address answered HTTP 404', 422))
  renderWithQuery(() => <SourcesPage />)
  fireEvent.change(await screen.findByLabelText('Feed URL'), { target: { value: 'https://news.example/nothing' } })
  fireEvent.click(screen.getByRole('button', { name: 'Test feed' }))

  expect(await screen.findByRole('alert')).toHaveTextContent('The address answered HTTP 404')
})

it('previews an OPML file, then adds the sources that are new', async () => {
  vi.mocked(api.previewOpml).mockResolvedValue({ items: [
    { name: 'Naftemporiki', url: 'https://naftemporiki.example/feed', tags: ['Greek', 'business'], source_country: 'GR', expected_language: 'el', existing_feed_id: null },
    { name: 'Daily', url: 'https://daily.example/feed', tags: [], source_country: null, expected_language: null, existing_feed_id: null },
    { name: 'Wire again', url: 'https://wire.example/rss', tags: [], source_country: null, expected_language: null, existing_feed_id: 'f1' },
  ], invalid: 2 })
  vi.mocked(api.importFeeds).mockResolvedValue({ created: 1, skipped: 0 })
  renderWithQuery(() => <SourcesPage />)
  const file = new File(['<opml version="2.0"><body/></opml>'], 'feeds.opml', { type: 'text/x-opml' })
  fireEvent.change(await screen.findByLabelText('Import OPML'), { target: { files: [file] } })

  const preview = await screen.findByRole('region', { name: 'OPML preview' })
  expect(api.previewOpml).toHaveBeenCalledWith('<opml version="2.0"><body/></opml>')
  expect(within(preview).getByText('2 entries were left out: they had no web address, or repeated one.')).toBeTruthy()
  expect(within(preview).getByRole('checkbox', { name: 'Naftemporiki' })).toBeChecked()
  expect(within(preview).getByText('Greek, business · Greece (GR) · Greek (el)')).toBeTruthy()
  expect(within(preview).getByText('Wire again')).toBeTruthy()
  expect(within(preview).getByText('Already a source')).toBeTruthy()
  expect(within(preview).queryByRole('checkbox', { name: 'Wire again' })).toBeNull()
  fireEvent.click(within(preview).getByRole('checkbox', { name: 'Daily' }))
  fireEvent.click(within(preview).getByRole('button', { name: 'Add 1 source' }))

  await waitFor(() => expect(api.importFeeds).toHaveBeenCalledWith([{ name: 'Naftemporiki', url: 'https://naftemporiki.example/feed', tags: ['Greek', 'business'], source_country: 'GR', expected_language: 'el' }]))
  expect(await screen.findByText('Added 1 source.')).toBeTruthy()
  expect(screen.queryByRole('region', { name: 'OPML preview' })).toBeNull()
})

it('says when a file is not OPML', async () => {
  vi.mocked(api.previewOpml).mockRejectedValue(new ApiError('This is not an OPML file', 422))
  renderWithQuery(() => <SourcesPage />)
  fireEvent.change(await screen.findByLabelText('Import OPML'), { target: { files: [new File(['<rss/>'], 'feed.xml')] } })
  expect(await screen.findByRole('alert')).toHaveTextContent('This is not an OPML file')
})

it('exports every source as OPML', async () => {
  renderWithQuery(() => <SourcesPage />)
  const link = await screen.findByRole('link', { name: 'Export OPML' })
  expect(link).toHaveAttribute('href', '/api/v1/feeds/opml')
  expect(link).toHaveAttribute('download')
})
