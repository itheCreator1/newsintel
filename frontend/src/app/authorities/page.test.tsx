import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../../lib/api'
import { resetNavigationHarness } from '../../test/navigation-harness'
import { renderWithQuery } from '../../test/render'
import AuthoritiesPage from './page'

vi.mock('../../lib/api', async importOriginal => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: { authorities: vi.fn(), authoritySuggestions: vi.fn(), authorityHistory: vi.fn(), mergeEntity: vi.fn(), addDistinct: vi.fn() },
}))

const root = (id: string, name: string, over = {}) => ({
  id, display_name: name, entity_type: 'PERSON', language: 'en', status: 'provisional' as const, ambiguous: false, variant_count: 0, ...over,
})
const name = (id: string, display: string, articles: number, type = 'PERSON') => ({ id, display_name: display, entity_type: type, article_count: articles })
const suggestion = (over = {}) => ({
  root: name('r1', 'Jon Tarr', 4), variant: name('v1', 'J. Tarr', 1), score: 0.85, reasons: ['initials', 'shared articles'], shared_articles: 1, ...over,
})
const change = (id: string, action: string, entityName: string | null, after: Record<string, unknown> | null = null) => ({
  id, action, entity_id: `e-${id}`, other_id: null, before: null, after, created_at: '2026-10-08T10:00:00Z', entity_name: entityName, other_name: null as string | null,
})

beforeEach(() => {
  vi.clearAllMocks()
  resetNavigationHarness({ pathname: '/authorities/' })
  vi.mocked(api.authorities).mockResolvedValue({
    items: [root('e1', 'Brandt, Hugo', { status: 'established', variant_count: 2 }), root('e2', 'Ines Alvar', { entity_type: 'ORG', language: 'el' })],
    next_cursor: null,
  })
  vi.mocked(api.authoritySuggestions).mockResolvedValue({ items: [suggestion()] })
  vi.mocked(api.authorityHistory).mockResolvedValue({ items: [], next_cursor: null })
  vi.mocked(api.mergeEntity).mockResolvedValue({ id: 'run', kind: 'merge', status: 'running', entity_id: 'v1', root_id: 'r1' })
  vi.mocked(api.addDistinct).mockResolvedValue(undefined)
})
afterEach(cleanup)

it('lists the authority file by name, with links, status and other names', async () => {
  renderWithQuery(() => <AuthoritiesPage />)

  const list = await screen.findByRole('list', { name: 'Authority file' })
  const rows = within(list).getAllByRole('listitem')
  expect(rows.map(row => within(row).getByRole('link').textContent)).toEqual(['Brandt, Hugo', 'Ines Alvar'])
  expect(within(rows[0]).getByRole('link')).toHaveAttribute('href', '/entities/?id=e1')
  expect(within(rows[0]).getByText('PERSON · en · 2 other names')).toBeTruthy()
  expect(within(rows[0]).getByText('Established')).toBeTruthy()
  expect(within(rows[1]).getByText('ORG · el')).toBeTruthy()
  expect(within(rows[1]).getByText('Provisional')).toBeTruthy()
  expect(api.authorities).toHaveBeenCalledWith({}, undefined)
})

it('filters by name, provisional status and language, and loads more', async () => {
  vi.mocked(api.authorities).mockImplementation(async (_filters, cursor) => cursor
    ? { items: [root('e9', 'Zora Lim')], next_cursor: null }
    : { items: [root('e1', 'Brandt, Hugo')], next_cursor: 'next' })
  renderWithQuery(() => <AuthoritiesPage />)

  fireEvent.click(await screen.findByRole('button', { name: 'Load more names' }))
  expect(await screen.findByRole('link', { name: 'Zora Lim' })).toBeTruthy()
  expect(api.authorities).toHaveBeenCalledWith({}, 'next')

  fireEvent.change(screen.getByLabelText('Find a name'), { target: { value: ' hug ' } })
  await waitFor(() => expect(api.authorities).toHaveBeenLastCalledWith({ q: 'hug' }, undefined))
  fireEvent.click(screen.getByLabelText('Provisional only'))
  await waitFor(() => expect(api.authorities).toHaveBeenLastCalledWith({ q: 'hug', status: 'provisional' }, undefined))
  fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'el' } })
  await waitFor(() => expect(api.authorities).toHaveBeenLastCalledWith({ q: 'hug', status: 'provisional', language: 'el' }, undefined))
  await waitFor(() => expect(api.authoritySuggestions).toHaveBeenLastCalledWith('el'))
})

it('offers likely duplicates with their reasons and article counts', async () => {
  renderWithQuery(() => <AuthoritiesPage />)

  const queue = await screen.findByRole('list', { name: 'Maybe the same?' })
  const item = within(queue).getByRole('listitem')
  expect(within(item).getByText('Is “J. Tarr” the same as “Jon Tarr”?')).toBeTruthy()
  expect(within(item).getByText('Initials · shared articles · 1 article together')).toBeTruthy()
  expect(within(item).getByText('PERSON · 1 article / 4 articles')).toBeTruthy()
  expect(within(item).getByRole('link', { name: 'J. Tarr' })).toHaveAttribute('href', '/entities/?id=v1')
  expect(within(item).getByRole('link', { name: 'Jon Tarr' })).toHaveAttribute('href', '/entities/?id=r1')
  expect(api.authoritySuggestions).toHaveBeenCalledWith(undefined)
})

it('approves a suggestion by merging the variant into the root, and refreshes the queue', async () => {
  renderWithQuery(() => <AuthoritiesPage />)

  fireEvent.click(await screen.findByRole('button', { name: 'Same entity: merge J. Tarr into Jon Tarr' }))
  await waitFor(() => expect(api.mergeEntity).toHaveBeenCalledWith('v1', 'r1'))
  expect(await screen.findByText('J. Tarr is now a name of Jon Tarr; its articles move over shortly.')).toBeTruthy()
  await waitFor(() => expect(api.authoritySuggestions).toHaveBeenCalledTimes(2))
  await waitFor(() => expect(api.authorityHistory).toHaveBeenCalledTimes(2))
})

it('can keep the other name instead', async () => {
  renderWithQuery(() => <AuthoritiesPage />)

  fireEvent.click(await screen.findByRole('button', { name: 'Same entity, keep J. Tarr: merge Jon Tarr into J. Tarr' }))
  await waitFor(() => expect(api.mergeEntity).toHaveBeenCalledWith('r1', 'v1'))
  expect(await screen.findByText('Jon Tarr is now a name of J. Tarr; its articles move over shortly.')).toBeTruthy()
})

it('rejects a suggestion by recording the pair as different', async () => {
  renderWithQuery(() => <AuthoritiesPage />)

  fireEvent.click(await screen.findByRole('button', { name: 'Different: keep J. Tarr apart from Jon Tarr' }))
  await waitFor(() => expect(api.addDistinct).toHaveBeenCalledWith('r1', 'v1'))
  expect(await screen.findByText('J. Tarr and Jon Tarr are recorded as different.')).toBeTruthy()
  await waitFor(() => expect(api.authoritySuggestions).toHaveBeenCalledTimes(2))
})

it('explains a refused decision and an empty queue', async () => {
  vi.mocked(api.mergeEntity).mockRejectedValue(new ApiError('These entities are marked as different', 409))
  renderWithQuery(() => <AuthoritiesPage />)

  fireEvent.click(await screen.findByRole('button', { name: 'Same entity: merge J. Tarr into Jon Tarr' }))
  expect((await screen.findByRole('alert')).textContent).toBe('These entities are marked as different')

  cleanup()
  vi.mocked(api.authoritySuggestions).mockResolvedValue({ items: [] })
  renderWithQuery(() => <AuthoritiesPage />)
  expect(await screen.findByText('No likely duplicates right now.')).toBeTruthy()
})

it('shows recent changes newest first, linked to both entities', async () => {
  vi.mocked(api.authorityHistory)
    .mockResolvedValueOnce({ items: [change('h2', 'renamed', 'Lund, Kai', { preferred_text: 'Lund, Kai' }), change('h1', 'status_changed', 'Lund, Kai', { status: 'established' })], next_cursor: 'older' })
    .mockResolvedValueOnce({ items: [{ ...change('h0', 'merged', 'NAC'), other_id: 'r9', other_name: 'North Atlantic Council' }, change('hx', 'ambiguous_changed', null, { ambiguous: true })], next_cursor: null })
  renderWithQuery(() => <AuthoritiesPage />)

  const list = await screen.findByRole('list', { name: 'Recent changes' })
  const rows = within(list).getAllByRole('listitem')
  expect(rows[0].textContent).toContain('Lund, Kai: renamed to Lund, Kai')
  expect(within(rows[0]).getByRole('link', { name: 'Lund, Kai' })).toHaveAttribute('href', '/entities/?id=e-h2')
  expect(rows[1].textContent).toContain('Lund, Kai: marked established')

  fireEvent.click(screen.getByRole('button', { name: 'Load older changes' }))
  const merged = (await within(list).findByText('NAC')).closest('li')!
  expect(merged.textContent).toContain('NAC merged into North Atlantic Council')
  expect(within(merged).getByRole('link', { name: 'North Atlantic Council' })).toHaveAttribute('href', '/entities/?id=r9')
  expect(within(list).getAllByRole('listitem')[3].textContent).toContain('Unknown entity: marked as an ambiguous name')
  expect(api.authorityHistory).toHaveBeenLastCalledWith('older')
})

it('says so when the file cannot be loaded', async () => {
  vi.mocked(api.authorities).mockRejectedValue(new Error('down'))
  vi.mocked(api.authoritySuggestions).mockRejectedValue(new Error('down'))
  renderWithQuery(() => <AuthoritiesPage />)

  expect(await screen.findByText('Could not load the authority file.')).toBeTruthy()
  expect(await screen.findByText('Could not load suggestions.')).toBeTruthy()
})
