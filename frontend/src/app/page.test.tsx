import { cleanup, fireEvent, screen } from '@testing-library/react'
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
