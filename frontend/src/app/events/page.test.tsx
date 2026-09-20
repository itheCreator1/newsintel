import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '../../lib/api'
import { navigationHarness, resetNavigationHarness } from '../../test/navigation-harness'
import { renderWithQuery } from '../../test/render'
import EventsPage from './page'

vi.mock('../../lib/api', async importOriginal => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: { events: vi.fn() },
}))

const event = (id: string, over = {}) => ({
  id, algorithm_version: 'rule-1', status: 'active', started_at: '2026-09-17T08:00:00Z', ended_at: '2026-09-18T10:00:00Z',
  primary_country: 'GR', cluster_count: 2, article_count: 5, source_count: 3, headline: `Headline ${id}`, headline_article_id: 'a1',
  entities: [{ id: 'ent-1', display_name: 'Barack Obama', entity_type: 'PERSON', article_count: 4 }], ...over,
})
const page = (items: ReturnType<typeof event>[], next: string | null = null) => ({ items, next_cursor: next })

beforeEach(() => {
  vi.clearAllMocks()
  resetNavigationHarness({ pathname: '/events/' })
  vi.mocked(api.events).mockResolvedValue(page([event('ev-1')]))
})
afterEach(cleanup)

it('lists events with headline, counts, country, entities and a link to the dossier', async () => {
  renderWithQuery(() => <EventsPage />)

  const link = await screen.findByRole('link', { name: 'Headline ev-1' })
  expect(link.getAttribute('href')).toMatch(/^\/events\/detail\/\?id=ev-1&from=/)
  expect(screen.getByText('2 stories · 5 articles · 3 sources')).toBeTruthy()
  expect(screen.getByText('active', { selector: 'span' })).toBeTruthy()
  expect(screen.getByText(/ · GR$/)).toBeTruthy()
  expect(screen.getByRole('link', { name: /Barack Obama/ }).getAttribute('href')).toBe('/entities/?id=ent-1')
  expect(api.events).toHaveBeenCalledWith({}, undefined)
})

it('falls back to a placeholder when an event has no headline', async () => {
  vi.mocked(api.events).mockResolvedValue(page([event('ev-2', { headline: null, headline_article_id: null })]))
  renderWithQuery(() => <EventsPage />)

  expect(await screen.findByRole('link', { name: 'Untitled event' })).toBeTruthy()
})

it('says so when nothing matches and when loading fails', async () => {
  vi.mocked(api.events).mockResolvedValueOnce(page([]))
  const first = renderWithQuery(() => <EventsPage />)
  expect(await screen.findByText('No events match these filters.')).toBeTruthy()
  first.unmount()

  vi.mocked(api.events).mockRejectedValueOnce(new Error('boom'))
  renderWithQuery(() => <EventsPage />)
  expect(await screen.findByText('Could not load events.')).toBeTruthy()
})

it('sends URL filters to the API', async () => {
  resetNavigationHarness({ pathname: '/events/', search: 'status=closed&country=gr&entity_id=ent-1&from=2026-09-01&to=2026-09-30' })
  renderWithQuery(() => <EventsPage />)

  await screen.findByRole('link', { name: 'Headline ev-1' })
  expect(api.events).toHaveBeenCalledWith({ status: 'closed', country: 'GR', entity_id: 'ent-1', from: '2026-09-01T00:00:00Z', to: '2026-09-30T23:59:59Z' }, undefined)
})

it('writes changed filters back to the URL and drops emptied ones', async () => {
  resetNavigationHarness({ pathname: '/events/', search: 'status=closed' })
  renderWithQuery(() => <EventsPage />)
  await screen.findByRole('link', { name: 'Headline ev-1' })

  fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'active' } })
  expect(navigationHarness.replace).toHaveBeenLastCalledWith('/events/?status=active')
  fireEvent.change(screen.getByLabelText('Country'), { target: { value: 'gr' } })
  expect(navigationHarness.replace).toHaveBeenLastCalledWith('/events/?status=closed&country=GR')
  fireEvent.change(screen.getByLabelText('From'), { target: { value: '2026-09-01' } })
  expect(navigationHarness.replace).toHaveBeenLastCalledWith('/events/?status=closed&from=2026-09-01')
  fireEvent.change(screen.getByLabelText('Status'), { target: { value: '' } })
  expect(navigationHarness.replace).toHaveBeenLastCalledWith('/events/')
})

it('shows and clears an entity filter', async () => {
  resetNavigationHarness({ pathname: '/events/', search: 'entity_id=ent-1&status=active' })
  renderWithQuery(() => <EventsPage />)
  await screen.findByRole('link', { name: 'Headline ev-1' })

  fireEvent.click(screen.getByRole('button', { name: 'Clear entity filter' }))
  expect(navigationHarness.replace).toHaveBeenLastCalledWith('/events/?status=active')
})

it('loads more events with the cursor until the last page', async () => {
  vi.mocked(api.events)
    .mockResolvedValueOnce(page([event('ev-1')], 'cursor-1'))
    .mockResolvedValueOnce(page([event('ev-2')], null))
  renderWithQuery(() => <EventsPage />)

  fireEvent.click(await screen.findByRole('button', { name: 'Load more events' }))

  expect(await screen.findByRole('link', { name: 'Headline ev-2' })).toBeTruthy()
  expect(screen.getByRole('link', { name: 'Headline ev-1' })).toBeTruthy()
  expect(api.events).toHaveBeenLastCalledWith({}, 'cursor-1')
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Load more events' })).toBeNull())
})
