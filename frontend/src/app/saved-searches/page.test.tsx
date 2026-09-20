import { cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../../lib/api'
import type { SavedSearch } from '../../lib/api-types'
import { resetNavigationHarness } from '../../test/navigation-harness'
import { renderWithQuery } from '../../test/render'
import SavedSearchesPage from './page'

vi.mock('../../lib/api', async importOriginal => ({ ...(await importOriginal<typeof import('../../lib/api')>()), api: { savedSearches: vi.fn(), updateSavedSearch: vi.fn(), deleteSavedSearch: vi.fn(), createMonitor: vi.fn() } }))

const saved = (id: string, name: string, overrides: Partial<SavedSearch> = {}): SavedSearch => ({
  id, name, state_version: 1, problem: null, created_at: '2026-09-15T08:00:00Z', updated_at: '2026-09-15T09:00:00Z',
  state: { q: 'grid', source_country: ['GR'], entity_id: ['entity-one'], before: '2026-02-01', sort: 'newest', interval: 'month' }, ...overrides,
})

beforeEach(() => {
  vi.clearAllMocks()
  resetNavigationHarness()
  vi.mocked(api.savedSearches).mockImplementation(async cursor => cursor
    ? { items: [saved('three', 'Retired filters', { state: null, problem: 'state.retired_filter: Extra inputs are not permitted' })], next_cursor: null }
    : { items: [saved('one', 'Greek grid'), saved('two', 'Shipping', { state: { q: 'port', sort: 'relevance', interval: 'auto' } })], next_cursor: 'page-two' })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('opens a saved search by restoring its complete URL state', async () => {
  renderWithQuery(() => <SavedSearchesPage />)

  expect(await screen.findByRole('link', { name: 'Open Greek grid' })).toHaveAttribute('href', '/search/?q=grid&country=GR&entity_id=entity-one&before=2026-02-01&sort=newest&interval=month')
})

it('pages with the cursor and explains a state that no longer validates', async () => {
  renderWithQuery(() => <SavedSearchesPage />)

  await fireEvent.click(await screen.findByRole('button', { name: 'Load more saved searches' }))

  expect(await screen.findByText('Retired filters')).toBeTruthy()
  expect(screen.getByText('This saved search can no longer be opened: state.retired_filter: Extra inputs are not permitted')).toBeTruthy()
  expect(screen.queryByRole('link', { name: 'Open Retired filters' })).toBeNull()
  expect(screen.getByRole('button', { name: 'Delete Retired filters' })).toBeTruthy()
  expect(api.savedSearches).toHaveBeenLastCalledWith('page-two')
})

it('renames a saved search and reports a duplicate name', async () => {
  vi.mocked(api.updateSavedSearch).mockRejectedValueOnce(new ApiError('A saved search with this name already exists', 409)).mockResolvedValueOnce(saved('one', 'Power grid'))
  renderWithQuery(() => <SavedSearchesPage />)
  await screen.findByRole('link', { name: 'Open Greek grid' })

  await fireEvent.click(screen.getByRole('button', { name: 'Rename Greek grid' }))
  fireEvent.change(screen.getByLabelText('New name for Greek grid'), { target: { value: 'Shipping' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Save name' }))
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'A saved search with this name already exists')

  fireEvent.change(screen.getByLabelText('New name for Greek grid'), { target: { value: '  Power grid ' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Save name' }))

  await vi.waitFor(() => expect(api.updateSavedSearch).toHaveBeenLastCalledWith('one', { name: 'Power grid' }))
  await vi.waitFor(() => expect(screen.queryByLabelText('New name for Greek grid')).toBeNull())
  expect(vi.mocked(api.savedSearches).mock.calls.length).toBeGreaterThan(1)
})

it('deletes a saved search only after confirmation', async () => {
  const confirm = vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true)
  vi.stubGlobal('confirm', confirm)
  vi.mocked(api.deleteSavedSearch).mockResolvedValue(undefined)
  renderWithQuery(() => <SavedSearchesPage />)

  await fireEvent.click(await screen.findByRole('button', { name: 'Delete Shipping' }))
  expect(api.deleteSavedSearch).not.toHaveBeenCalled()
  await fireEvent.click(screen.getByRole('button', { name: 'Delete Shipping' }))

  await vi.waitFor(() => expect(api.deleteSavedSearch).toHaveBeenCalledWith('two'))
  expect(confirm).toHaveBeenLastCalledWith('Delete the saved search “Shipping”?')
})

it('watches a saved search with a copy of its state, hiding the button when it cannot be opened', async () => {
  vi.mocked(api.createMonitor).mockRejectedValueOnce(new ApiError('A monitor with this name already exists', 409)).mockResolvedValueOnce({} as never)
  renderWithQuery(() => <SavedSearchesPage />)

  await fireEvent.click(await screen.findByRole('button', { name: 'Watch Greek grid' }))
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'A monitor with this name already exists')
  await fireEvent.click(screen.getByRole('button', { name: 'Watch Greek grid' }))

  await vi.waitFor(() => expect(api.createMonitor).toHaveBeenLastCalledWith('Greek grid', saved('one', 'Greek grid').state))
  expect(await screen.findByText('Watching “Greek grid”.')).toBeTruthy()
  expect(screen.getByRole('link', { name: 'Open watchlist' })).toHaveAttribute('href', '/monitors/')
  await fireEvent.click(screen.getByRole('button', { name: 'Load more saved searches' }))
  await screen.findByText('Retired filters')
  expect(screen.queryByRole('button', { name: 'Watch Retired filters' })).toBeNull()
})
