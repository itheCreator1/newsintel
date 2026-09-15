import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../api'
import type { SavedSearch } from '../api-types'
import SavedSearchesView from './SavedSearchesView.vue'

vi.mock('../api', async importOriginal => ({ ...(await importOriginal<typeof import('../api')>()), api: { savedSearches: vi.fn(), updateSavedSearch: vi.fn(), deleteSavedSearch: vi.fn() } }))

const saved = (id: string, name: string, overrides: Partial<SavedSearch> = {}): SavedSearch => ({
  id, name, state_version: 1, problem: null, created_at: '2026-09-15T08:00:00Z', updated_at: '2026-09-15T09:00:00Z',
  state: { q: 'grid', source_country: ['GR'], entity_id: ['entity-one'], before: '2026-02-01', sort: 'newest', interval: 'month' }, ...overrides,
})

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.savedSearches).mockImplementation(async cursor => cursor
    ? { items: [saved('three', 'Retired filters', { state: null, problem: 'state.retired_filter: Extra inputs are not permitted' })], next_cursor: null }
    : { items: [saved('one', 'Greek grid'), saved('two', 'Shipping', { state: { q: 'port', sort: 'relevance', interval: 'auto' } })], next_cursor: 'page-two' })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

async function renderSaved() {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/saved-searches', component: SavedSearchesView }, { path: '/search', component: { template: '<div />' } }] })
  await router.push('/saved-searches'); await router.isReady()
  render(SavedSearchesView, { global: { plugins: [[VueQueryPlugin, { queryClient: new QueryClient({ defaultOptions: { queries: { retry: false } } }) }], router] } })
  return router
}

it('opens a saved search by restoring its complete URL state', async () => {
  const router = await renderSaved()

  await fireEvent.click(await screen.findByRole('link', { name: 'Open Greek grid' }))

  await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/search'))
  expect(router.currentRoute.value.query).toEqual({ q: 'grid', country: 'GR', entity_id: 'entity-one', before: '2026-02-01', sort: 'newest', interval: 'month' })
})

it('pages with the cursor and explains a state that no longer validates', async () => {
  await renderSaved()

  await fireEvent.click(await screen.findByRole('button', { name: 'Load more saved searches' }))

  expect(await screen.findByText('Retired filters')).toBeTruthy()
  expect(screen.getByText('This saved search can no longer be opened: state.retired_filter: Extra inputs are not permitted')).toBeTruthy()
  expect(screen.queryByRole('link', { name: 'Open Retired filters' })).toBeNull()
  expect(screen.getByRole('button', { name: 'Delete Retired filters' })).toBeTruthy()
  expect(api.savedSearches).toHaveBeenLastCalledWith('page-two')
})

it('renames a saved search and reports a duplicate name', async () => {
  vi.mocked(api.updateSavedSearch).mockRejectedValueOnce(new ApiError('A saved search with this name already exists', 409)).mockResolvedValueOnce(saved('one', 'Power grid'))
  await renderSaved()

  await fireEvent.click(await screen.findByRole('button', { name: 'Rename Greek grid' }))
  await fireEvent.update(screen.getByLabelText('New name for Greek grid'), 'Shipping')
  await fireEvent.click(screen.getByRole('button', { name: 'Save name' }))
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'A saved search with this name already exists')

  await fireEvent.update(screen.getByLabelText('New name for Greek grid'), '  Power grid ')
  await fireEvent.click(screen.getByRole('button', { name: 'Save name' }))

  await vi.waitFor(() => expect(api.updateSavedSearch).toHaveBeenLastCalledWith('one', { name: 'Power grid' }))
  await vi.waitFor(() => expect(screen.queryByLabelText('New name for Greek grid')).toBeNull())
  expect(vi.mocked(api.savedSearches).mock.calls.length).toBeGreaterThan(1)
})

it('deletes a saved search only after confirmation', async () => {
  const confirm = vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true)
  vi.stubGlobal('confirm', confirm)
  vi.mocked(api.deleteSavedSearch).mockResolvedValue(undefined)
  await renderSaved()

  await fireEvent.click(await screen.findByRole('button', { name: 'Delete Shipping' }))
  expect(api.deleteSavedSearch).not.toHaveBeenCalled()
  await fireEvent.click(screen.getByRole('button', { name: 'Delete Shipping' }))

  await vi.waitFor(() => expect(api.deleteSavedSearch).toHaveBeenCalledWith('two'))
  expect(confirm).toHaveBeenLastCalledWith('Delete the saved search “Shipping”?')
})
