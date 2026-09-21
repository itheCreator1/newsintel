import { focusManager } from '@tanstack/react-query'
import { act, cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '../lib/api'
import { navigationHarness, resetNavigationHarness } from '../test/navigation-harness'
import { renderWithQuery } from '../test/render'
import OverviewPage from './page'

vi.mock('../lib/api', async importOriginal => ({ ...(await importOriginal<typeof import('../lib/api')>()), api: { status: vi.fn(), ingestionTimeline: vi.fn(), topEntities: vi.fn(), topCountries: vi.fn() } }))
// ECharts needs a canvas, so each chart is replaced by a control that emits its first item on click.
vi.mock('../components/BarChart', () => ({
  BarChart: (props: { items: { id: string }[]; ariaLabel: string; onSelect: (item: { id: string }) => void }) =>
    <button type="button" onClick={() => props.onSelect(props.items[0])}>{props.ariaLabel}</button>,
}))

beforeEach(() => {
  vi.clearAllMocks()
  resetNavigationHarness({ pathname: '/' })
  vi.mocked(api.status).mockResolvedValue({ status: 'ok' })
  vi.mocked(api.ingestionTimeline).mockResolvedValue({ buckets: [
    { date: '2026-09-01', count: 3, is_spike: false },
    { date: '2026-09-02', count: 9, is_spike: true },
  ] })
  vi.mocked(api.topEntities).mockResolvedValue({ entities: [{ entity_id: 'entity-one', display_text: 'Acme', entity_type: 'ORG', count: 5 }] })
  vi.mocked(api.topCountries).mockResolvedValue({ countries: [{ country_code: 'GR', count: 4 }] })
})
afterEach(cleanup)

it('opens a date-filtered search when the ingestion timeline is clicked', async () => {
  renderWithQuery(() => <OverviewPage />)

  await fireEvent.click(await screen.findByRole('button', { name: /Articles ingested per day/ }))

  expect(navigationHarness.pathname).toBe('/search/')
  expect(navigationHarness.searchParams.get('after')).toBe('2026-09-01')
  expect(navigationHarness.searchParams.get('before')).toBe('2026-09-02')
})

it('opens an entity-filtered search when a top entity bar is clicked', async () => {
  renderWithQuery(() => <OverviewPage />)

  await fireEvent.click(await screen.findByRole('button', { name: /Top named entities/ }))

  expect(navigationHarness.pathname).toBe('/search/')
  expect(navigationHarness.searchParams.getAll('entity_id')).toEqual(['entity-one'])
})

it('opens a country-filtered search when a top country bar is clicked', async () => {
  renderWithQuery(() => <OverviewPage />)

  await fireEvent.click(await screen.findByRole('button', { name: /Top primary story countries/ }))

  expect(navigationHarness.pathname).toBe('/search/')
  expect(navigationHarness.searchParams.getAll('story_country')).toEqual(['GR'])
})

it('refetches top entities when the entity type filter changes', async () => {
  renderWithQuery(() => <OverviewPage />)
  await screen.findByRole('button', { name: /Top named entities/ })

  fireEvent.change(screen.getByLabelText('Entity type'), { target: { value: 'PERSON' } })

  await vi.waitFor(() => expect(api.topEntities).toHaveBeenLastCalledWith('PERSON'))
})

it('shows an empty-state message instead of a chart when a panel has no data', async () => {
  vi.mocked(api.ingestionTimeline).mockResolvedValue({ buckets: [] })
  vi.mocked(api.topEntities).mockResolvedValue({ entities: [] })
  vi.mocked(api.topCountries).mockResolvedValue({ countries: [] })
  renderWithQuery(() => <OverviewPage />)

  expect(await screen.findByText('No articles ingested yet.')).toBeTruthy()
  expect(await screen.findByText('No entities found yet.')).toBeTruthy()
  expect(await screen.findByText('No countries found yet.')).toBeTruthy()
  expect(screen.queryByRole('button', { name: /Articles ingested per day/ })).toBeNull()
})

it('announces loading, then points an empty archive at its sources', async () => {
  vi.mocked(api.ingestionTimeline).mockResolvedValue({ buckets: [] })
  renderWithQuery(() => <OverviewPage />)

  expect(screen.getAllByRole('status').map(status => status.textContent)).toContain('Loading ingestion timeline…')
  expect(await screen.findByRole('link', { name: 'Manage sources' })).toHaveAttribute('href', '/sources/')
})

it('retries a failed panel and shows its data once the retry succeeds', async () => {
  vi.mocked(api.topCountries).mockRejectedValueOnce(new Error('offline'))
  renderWithQuery(() => <OverviewPage />)

  expect(await screen.findByText('Could not load top countries.')).toBeTruthy()
  expect(screen.queryByText('No countries found yet.')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  expect(await screen.findByRole('button', { name: /Top primary story countries/ })).toBeTruthy()
  expect(screen.queryByText('Could not load top countries.')).toBeNull()
})

it('keeps a loaded chart when a background refetch fails', async () => {
  renderWithQuery(() => <OverviewPage />)
  await screen.findByRole('button', { name: /Top primary story countries/ })
  vi.mocked(api.topCountries).mockRejectedValueOnce(new Error('offline'))
  act(() => { focusManager.setFocused(false); focusManager.setFocused(true) })

  expect(await screen.findByText('Could not load top countries.')).toBeTruthy()
  expect(screen.getByRole('button', { name: /Top primary story countries/ })).toBeTruthy()
  focusManager.setFocused(undefined)
})

it('says core services are unavailable when the status check fails, not that it is still checking', async () => {
  vi.mocked(api.status).mockRejectedValue(new Error('503 Service Unavailable'))
  renderWithQuery(() => <OverviewPage />)

  expect(await screen.findByText('Unavailable')).toBeTruthy()
  expect(screen.queryByText('Checking')).toBeNull()
})
