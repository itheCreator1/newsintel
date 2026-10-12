import { cleanup, fireEvent, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../../lib/api'
import type { Investigation, Monitor, SavedSearch } from '../../lib/api-types'
import { navigationHarness, resetNavigationHarness } from '../../test/navigation-harness'
import { monitor } from '../../test/monitors'
import { renderWithQuery } from '../../test/render'
import InvestigationsPage from './page'

vi.mock('../../lib/api', async importOriginal => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: { investigations: vi.fn(), watchSavedSearch: vi.fn(), updateSavedSearch: vi.fn(), deleteSavedSearch: vi.fn(), updateMonitor: vi.fn(), deleteMonitor: vi.fn() },
}))

const NOW = Date.parse('2026-09-20T12:00:00Z')
const saved = (id: string, name: string, overrides: Partial<SavedSearch> = {}): SavedSearch => ({
  id, name, state_version: 1, problem: null, created_at: '2026-09-15T08:00:00Z', updated_at: '2026-09-15T09:00:00Z',
  state: { q: 'grid', source_country: ['GR'], entity_id: ['entity-one'], before: '2026-02-01', sort: 'newest', interval: 'month' }, ...overrides,
})
const searchRow = (item: SavedSearch, watching: Monitor | null = null): Investigation => ({ id: item.id, name: item.name, saved_search: item, monitor: watching })
const monitorRow = (item: Monitor): Investigation => ({ id: item.id, name: item.name, saved_search: null, monitor: item })
const unseen = { unseen_article_count: 2, unseen_cluster_count: 1 }

const greek = saved('s1', 'Greek grid')
const shipping = saved('s2', 'Shipping', { state: { q: 'port', sort: 'relevance', interval: 'auto' } })
const shippingMonitor = monitor({ id: 'm2', name: 'Shipping', ...unseen })
const acme = monitor({ id: 'm1', name: 'Acme', kind: 'entity', state: { q: '', entity_id: ['e1'], sort: 'relevance', interval: 'auto' } })

beforeEach(() => {
  vi.clearAllMocks()
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
  resetNavigationHarness({ pathname: '/investigations/' })
  vi.mocked(api.investigations).mockResolvedValue({ items: [searchRow(shipping, shippingMonitor), monitorRow(acme), searchRow(greek)], next_cursor: null })
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals() })

const row = (name: string) => screen.findByRole('article', { name })
const watchSwitch = (name: string) => screen.getByRole('switch', { name: `Watch ${name}` })

it('lists saved searches and monitors together, newest activity first, with what is new', async () => {
  renderWithQuery(() => <InvestigationsPage />)

  const busy = await row('Shipping')
  expect(busy).toHaveAttribute('data-unseen', 'true')
  expect(within(busy).getByText('2 new articles · 1 new story')).toBeTruthy()
  // Watched: Open goes to the new results; the full search is one more link.
  expect(within(busy).getByRole('link', { name: 'Open Shipping' })).toHaveAttribute('href', '/monitors/?id=m2')
  expect(within(busy).getByRole('link', { name: 'Search Shipping' })).toHaveAttribute('href', '/search/?q=port')
  expect(within(busy).getByRole('switch', { name: 'Watch Shipping' })).toHaveAttribute('aria-checked', 'true')

  const entity = await row('Acme')
  expect(within(entity).getByText('Entity')).toBeTruthy()
  expect(within(entity).getByText('Nothing new')).toBeTruthy()

  const plain = await row('Greek grid')
  expect(plain).toHaveAttribute('data-unseen', 'false')
  expect(within(plain).getByText('Not watched')).toBeTruthy()
  expect(within(plain).getByRole('link', { name: 'Open Greek grid' })).toHaveAttribute('href', '/search/?q=grid&country=GR&entity_id=entity-one&before=2026-02-01&sort=newest&interval=month')
  expect(within(plain).getByRole('switch', { name: 'Watch Greek grid' })).toHaveAttribute('aria-checked', 'false')
  expect(screen.getByRole('link', { name: 'New search' })).toHaveAttribute('href', '/search/')
  expect(api.investigations).toHaveBeenCalledWith('activity', undefined)
})

it('says what it is doing while loading, when empty and when the request fails', async () => {
  vi.mocked(api.investigations).mockReturnValueOnce(new Promise(() => {}))
  const first = renderWithQuery(() => <InvestigationsPage />)
  expect(screen.getByText('Loading investigations…')).toBeTruthy()
  first.unmount()

  vi.mocked(api.investigations).mockResolvedValueOnce({ items: [], next_cursor: null })
  const second = renderWithQuery(() => <InvestigationsPage />)
  expect(await screen.findByText(/No investigations yet/)).toBeTruthy()
  second.unmount()

  vi.mocked(api.investigations).mockRejectedValueOnce(new ApiError('boom', 500))
  renderWithQuery(() => <InvestigationsPage />)
  expect(await screen.findByText('Could not load investigations.')).toBeTruthy()
})

it('keeps the order in the URL and pages with the cursor', async () => {
  vi.mocked(api.investigations).mockImplementation(async (_order, cursor) => cursor
    ? { items: [searchRow(saved('s9', 'Later'))], next_cursor: null }
    : { items: [searchRow(greek)], next_cursor: 'page-two' })
  const view = renderWithQuery(() => <InvestigationsPage />)

  await fireEvent.click(await screen.findByRole('button', { name: 'Load more investigations' }))
  expect(await row('Later')).toBeTruthy()
  expect(api.investigations).toHaveBeenLastCalledWith('activity', 'page-two')

  fireEvent.change(screen.getByLabelText('Order'), { target: { value: 'name' } })
  expect(navigationHarness.push).toHaveBeenCalledWith('/investigations/?order=name')
  view.rerenderSame()
  await vi.waitFor(() => expect(api.investigations).toHaveBeenLastCalledWith('name', undefined))
})

it('watches a saved search in place, pauses a watched one, and resumes a paused monitor', async () => {
  vi.mocked(api.investigations).mockResolvedValue({ items: [
    searchRow(shipping, shippingMonitor), monitorRow({ ...acme, enabled: false }), searchRow(greek),
  ], next_cursor: null })
  vi.mocked(api.watchSavedSearch).mockRejectedValueOnce(new ApiError('The monitor “greek GRID” already has this name. Rename one of them first.', 409)).mockResolvedValue(monitor())
  vi.mocked(api.updateMonitor).mockResolvedValue(monitor())
  renderWithQuery(() => <InvestigationsPage />)
  await row('Greek grid')

  expect(within(await row('Acme')).getByText('Paused')).toBeTruthy()
  await fireEvent.click(watchSwitch('Greek grid'))
  expect(await screen.findByRole('alert')).toHaveTextContent('The monitor “greek GRID” already has this name. Rename one of them first.')
  await fireEvent.click(watchSwitch('Greek grid'))
  await vi.waitFor(() => expect(api.watchSavedSearch).toHaveBeenLastCalledWith('s1'))

  await fireEvent.click(watchSwitch('Shipping'))
  await vi.waitFor(() => expect(api.updateMonitor).toHaveBeenLastCalledWith('m2', { enabled: false }))
  await fireEvent.click(watchSwitch('Acme'))
  await vi.waitFor(() => expect(api.updateMonitor).toHaveBeenLastCalledWith('m1', { enabled: true }))
  await vi.waitFor(() => expect(vi.mocked(api.investigations).mock.calls.length).toBeGreaterThan(1))
})

it('cannot watch or open a saved search whose state no longer validates, but can delete it', async () => {
  vi.mocked(api.investigations).mockResolvedValue({ items: [searchRow(saved('s3', 'Retired filters', { state: null, problem: 'state.retired_filter: Extra inputs are not permitted' }))], next_cursor: null })
  renderWithQuery(() => <InvestigationsPage />)

  const broken = await row('Retired filters')
  expect(within(broken).getByText('This saved search can no longer be opened: state.retired_filter: Extra inputs are not permitted')).toBeTruthy()
  expect(within(broken).queryByRole('link', { name: 'Open Retired filters' })).toBeNull()
  expect(within(broken).queryByRole('switch')).toBeNull()
  expect(within(broken).getByRole('button', { name: 'Delete Retired filters' })).toBeTruthy()
})

it('renames through the saved search when there is one, else the monitor, reporting a duplicate', async () => {
  vi.mocked(api.updateSavedSearch).mockRejectedValueOnce(new ApiError('A saved search or monitor with this name already exists', 409)).mockResolvedValue(greek)
  vi.mocked(api.updateMonitor).mockResolvedValue(acme)
  renderWithQuery(() => <InvestigationsPage />)
  await row('Greek grid')

  await fireEvent.click(screen.getByRole('button', { name: 'Rename Greek grid' }))
  fireEvent.change(screen.getByLabelText('New name for Greek grid'), { target: { value: 'Shipping' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Save name' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('A saved search or monitor with this name already exists')
  fireEvent.change(screen.getByLabelText('New name for Greek grid'), { target: { value: '  Power grid ' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Save name' }))
  await vi.waitFor(() => expect(api.updateSavedSearch).toHaveBeenLastCalledWith('s1', { name: 'Power grid' }))
  await vi.waitFor(() => expect(screen.queryByLabelText('New name for Greek grid')).toBeNull())

  await fireEvent.click(screen.getByRole('button', { name: 'Rename Acme' }))
  fireEvent.change(screen.getByLabelText('New name for Acme'), { target: { value: 'Acme Corp' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Save name' }))
  await vi.waitFor(() => expect(api.updateMonitor).toHaveBeenLastCalledWith('m1', { name: 'Acme Corp' }))
})

it('deletes only after confirmation, saying what goes with it', async () => {
  const confirm = vi.fn().mockReturnValueOnce(false).mockReturnValue(true)
  vi.stubGlobal('confirm', confirm)
  vi.mocked(api.deleteSavedSearch).mockResolvedValue(undefined)
  vi.mocked(api.deleteMonitor).mockResolvedValue(undefined)
  renderWithQuery(() => <InvestigationsPage />)
  await row('Shipping')

  await fireEvent.click(screen.getByRole('button', { name: 'Delete Shipping' }))
  expect(api.deleteSavedSearch).not.toHaveBeenCalled()
  await fireEvent.click(screen.getByRole('button', { name: 'Delete Shipping' }))
  await vi.waitFor(() => expect(api.deleteSavedSearch).toHaveBeenCalledWith('s2'))
  expect(confirm).toHaveBeenLastCalledWith('Delete “Shipping” and stop watching it?')

  await fireEvent.click(screen.getByRole('button', { name: 'Delete Acme' }))
  await vi.waitFor(() => expect(api.deleteMonitor).toHaveBeenCalledWith('m1'))
  expect(confirm).toHaveBeenLastCalledWith('Delete “Acme”?')
})
