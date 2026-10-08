import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../lib/api'
import type { WikidataLink } from '../lib/api-types'
import { resetNavigationHarness } from '../test/navigation-harness'
import { renderWithQuery } from '../test/render'
import { EntityWikidata } from './EntityWikidata'

vi.mock('../lib/api', async importOriginal => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: {
    wikidata: vi.fn(), linkWikidata: vi.fn(), unlinkWikidata: vi.fn(), addWikidataNames: vi.fn(),
    searchWikidata: vi.fn(), refreshWikidata: vi.fn(), dismissWikidataCandidate: vi.fn(),
  },
}))

const AT = '2026-10-08T10:00:00Z'
const candidate = (qid: string, over = {}) => ({
  qid, score: 0.9, reasons: ['label:en', 'type_matches', 'sitelinks:120'], exact: true,
  label: 'Nora Vale', description: 'Norwegian sprinter', sitelinks: 120, ...over,
})
const item = (over = {}) => ({
  qid: 'Q42', state: 'ok', redirect_to: null, revision: 100,
  labels: { en: 'Nora Vale', el: 'Νόρα Βέιλ' }, aliases: { en: ['N. Vale'] }, descriptions: { en: 'Norwegian sprinter' },
  instance_of: ['Q5'], different_from: [], sitelinks: 120, claims_fetched: true, fetched_at: AT, checked_at: AT, ...over,
})
const link = (over: Partial<WikidataLink> = {}): WikidataLink => ({
  entity_id: 'ent-1', qid: null, identifiers: {}, item: null, fetch_pending: false, names: [],
  candidates: [], search_pending: false, redirect_holder: null, ...over,
})
const linked = (over: Partial<WikidataLink> = {}) => link({
  qid: 'Q42', item: item(), identifiers: { viaf: '113230702', isni: '0000 0001 2144 1970' },
  names: [
    { language: 'en', text: 'Nora Vale', kind: 'label', status: 'this_entity', entity_id: 'ent-1' },
    { language: 'en', text: 'N. Vale', kind: 'alias', status: 'absent', entity_id: null },
    { language: 'el', text: 'Βέιλ', kind: 'alias', status: 'other_entity', entity_id: 'ent-7' },
  ],
  ...over,
})
const run = { id: 'run', kind: 'candidates', status: 'queued', entity_id: 'ent-1', checked: 0, changed: 0, redirected: 0, missing: 0, errors: 0, requests: 0, error: null, created_at: AT, started_at: null, finished_at: null }

const render = () => renderWithQuery(() => <EntityWikidata entityId="ent-1" language="en" />)

beforeEach(() => {
  vi.clearAllMocks()
  resetNavigationHarness({ pathname: '/entities/', search: 'id=ent-1' })
  vi.mocked(api.wikidata).mockResolvedValue(link())
  vi.mocked(api.searchWikidata).mockResolvedValue(run)
  vi.mocked(api.refreshWikidata).mockResolvedValue({ ...run, kind: 'refresh' })
  vi.mocked(api.dismissWikidataCandidate).mockResolvedValue(undefined)
  vi.mocked(api.unlinkWikidata).mockResolvedValue(undefined)
})
afterEach(cleanup)

it('lists the suggested items with their reasons, and links or dismisses one', async () => {
  vi.mocked(api.wikidata).mockResolvedValue(link({ candidates: [candidate('Q42'), candidate('Q7', { exact: false, label: 'Nora Vale', description: 'painter', reasons: ['alias:en', 'type_differs'], sitelinks: 3 })] }))
  vi.mocked(api.linkWikidata).mockResolvedValue(linked())
  render()

  const list = await screen.findByRole('list', { name: 'Wikidata suggestions' })
  const rows = within(list).getAllByRole('listitem')
  expect(within(rows[0]).getByRole('link', { name: 'Q42' })).toHaveAttribute('href', 'https://www.wikidata.org/wiki/Q42')
  expect(within(rows[0]).getByText('Norwegian sprinter')).toBeTruthy()
  expect(within(rows[0]).getByText('Exact label (en) · Type matches · 120 sitelinks')).toBeTruthy()
  expect(within(rows[1]).getByText('Exact alias (en) · Type differs')).toBeTruthy()
  expect(screen.getByText(/Data from/)).toHaveTextContent('Data from Wikidata (CC0)')

  fireEvent.click(within(rows[1]).getByRole('button', { name: 'Not this one: Q7' }))
  await waitFor(() => expect(api.dismissWikidataCandidate).toHaveBeenCalledWith('ent-1', 'Q7'))

  fireEvent.click(within(rows[0]).getByRole('button', { name: 'Link to Q42' }))
  await waitFor(() => expect(api.linkWikidata).toHaveBeenCalledWith('ent-1', 'Q42', []))
})

it('says when there are no suggestions, asks Wikidata on request and waits for the search', async () => {
  vi.mocked(api.wikidata)
    .mockResolvedValueOnce(link())
    .mockResolvedValue(link({ search_pending: true }))
  render()

  expect(await screen.findByText('No Wikidata suggestions yet.')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Find on Wikidata' }))
  await waitFor(() => expect(api.searchWikidata).toHaveBeenCalledWith('ent-1'))
  expect(await screen.findByText('Searching Wikidata…')).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Find on Wikidata' })).toBeDisabled()
})

it('links an item typed by hand and checks it looks like an item id', async () => {
  vi.mocked(api.linkWikidata).mockResolvedValue(linked())
  render()

  const field = await screen.findByLabelText('Wikidata item id')
  fireEvent.change(field, { target: { value: 'nora' } })
  expect(screen.getByRole('button', { name: 'Link item' })).toBeDisabled()
  fireEvent.change(field, { target: { value: ' q42 ' } })
  fireEvent.click(screen.getByRole('button', { name: 'Link item' }))
  await waitFor(() => expect(api.linkWikidata).toHaveBeenCalledWith('ent-1', 'Q42', []))
})

it('names the entity that already holds the item when linking is refused', async () => {
  vi.mocked(api.linkWikidata).mockRejectedValue(new ApiError('Q42 is already linked to Nora Vale; merge the two entities instead', 409,
    { message: 'Q42 is already linked to Nora Vale; merge the two entities instead', entity_id: 'ent-9', display_name: 'Nora Vale' }))
  render()

  fireEvent.change(await screen.findByLabelText('Wikidata item id'), { target: { value: 'Q42' } })
  fireEvent.click(screen.getByRole('button', { name: 'Link item' }))
  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent('Q42 is already linked to Nora Vale; merge the two entities instead')
  expect(within(alert).getByRole('link', { name: 'Open Nora Vale' })).toHaveAttribute('href', '/entities/?id=ent-9')
})

it('shows a linked item with its identifiers, refreshes and unlinks it', async () => {
  vi.mocked(api.wikidata).mockResolvedValue(linked())
  render()

  expect(await screen.findByRole('link', { name: 'Q42' })).toHaveAttribute('href', 'https://www.wikidata.org/wiki/Q42')
  expect(screen.getByText('Nora Vale · Norwegian sprinter')).toBeTruthy()
  const ids = screen.getByRole('list', { name: 'Identifiers' })
  expect(within(ids).getByRole('link', { name: 'VIAF 113230702' })).toHaveAttribute('href', 'https://viaf.org/viaf/113230702')
  expect(within(ids).getByRole('link', { name: 'ISNI 0000 0001 2144 1970' })).toHaveAttribute('href', 'https://isni.org/isni/0000000121441970')
  expect(screen.queryByRole('button', { name: 'Find on Wikidata' })).toBeNull()

  fireEvent.click(screen.getByRole('button', { name: 'Refresh from Wikidata' }))
  await waitFor(() => expect(api.refreshWikidata).toHaveBeenCalledWith('ent-1'))
  expect(await screen.findByRole('status')).toHaveTextContent('Refresh queued')

  fireEvent.click(screen.getByRole('button', { name: 'Unlink' }))
  await waitFor(() => expect(api.unlinkWikidata).toHaveBeenCalledWith('ent-1'))
})

it('offers the item names this entity lacks and adds the ticked ones', async () => {
  vi.mocked(api.wikidata).mockResolvedValue(linked())
  vi.mocked(api.addWikidataNames).mockResolvedValue(linked())
  render()

  const offered = await screen.findByRole('list', { name: 'Names on Wikidata' })
  expect(within(offered).getAllByRole('checkbox')).toHaveLength(1)
  expect(within(offered).getByRole('link', { name: 'Βέιλ (el)' })).toHaveAttribute('href', '/entities/?id=ent-7')
  expect(within(offered).getByText(/already a name of another entity/)).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Add selected names' })).toBeDisabled()

  fireEvent.click(within(offered).getByRole('checkbox', { name: 'N. Vale (en, alias)' }))
  fireEvent.click(screen.getByRole('button', { name: 'Add selected names' }))
  await waitFor(() => expect(api.addWikidataNames).toHaveBeenCalledWith('ent-1', [{ language: 'en', text: 'N. Vale' }]))
})

it('says when the item is still being fetched, was merged, or was deleted', async () => {
  vi.mocked(api.wikidata).mockResolvedValue(linked({ item: null, fetch_pending: true, names: [], identifiers: {} }))
  render()
  expect(await screen.findByText('Fetching the item from Wikidata…')).toBeTruthy()
  cleanup()

  vi.mocked(api.wikidata).mockResolvedValue(linked({
    item: item({ state: 'redirected', redirect_to: 'Q99' }), names: [], redirect_holder: { entity_id: 'ent-5', display_name: 'N. Vale' },
  }))
  render()
  const merged = await screen.findByText(/Wikidata merged Q42 into Q99/)
  expect(merged).toHaveTextContent('Wikidata merged Q42 into Q99, which N. Vale is linked to. If they are the same, merge the two entities.')
  expect(within(merged).getByRole('link', { name: 'N. Vale' })).toHaveAttribute('href', '/entities/?id=ent-5')
  cleanup()

  vi.mocked(api.wikidata).mockResolvedValue(linked({ item: item({ state: 'missing', labels: {}, descriptions: {} }), names: [] }))
  render()
  expect(await screen.findByText('Wikidata no longer has Q42. The link is kept; unlink it if it is wrong.')).toBeTruthy()
})
