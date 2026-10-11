import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../../lib/api'
import { navigationHarness, resetNavigationHarness } from '../../test/navigation-harness'
import { renderWithQuery } from '../../test/render'
import AuthoritiesPage from './page'

vi.mock('../../lib/api', async importOriginal => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: {
    authorities: vi.fn(), authoritySuggestions: vi.fn(), authorityHistory: vi.fn(), mergeEntity: vi.fn(), addDistinct: vi.fn(), bulkAuthorities: vi.fn(),
    wikidataCandidates: vi.fn(), approveExactWikidata: vi.fn(), linkWikidata: vi.fn(), dismissWikidataCandidate: vi.fn(),
    entityDossier: vi.fn(), entityVariants: vi.fn(), entityHistory: vi.fn(), wikidata: vi.fn(), seeAlso: vi.fn(), updateEntity: vi.fn(), nlpEntities: vi.fn(),
  },
}))

const root = (id: string, name: string, over = {}) => ({
  id, display_name: name, entity_type: 'PERSON', language: 'en', status: 'provisional' as const, ambiguous: false, hidden: false, variant_count: 0, article_count: 0, qid: null as string | null, ...over,
})
const name = (id: string, display: string, articles: number, type = 'PERSON') => ({ id, display_name: display, entity_type: type, article_count: articles })
const suggestion = (over = {}) => ({
  root: name('r1', 'Jon Tarr', 4, 'ORG'), variant: name('v1', 'J. Tarr', 1), score: 0.85, reasons: ['initials', 'shared articles'], shared_articles: 1, ...over,
})
const review = (entityId: string, displayName: string, qid: string, over = {}) => ({
  entity_id: entityId, display_name: displayName, entity_type: 'PERSON', language: 'en', qid, score: 0.9,
  reasons: ['label:en', 'type_matches'], exact: true, label: displayName, description: 'Danish runner', sitelinks: 12, ...over,
})
const change = (id: string, action: string, entityName: string | null, after: Record<string, unknown> | null = null) => ({
  id, action, entity_id: `e-${id}`, other_id: null, before: null as Record<string, unknown> | null, after, created_at: '2026-10-08T10:00:00Z', entity_name: entityName, other_name: null as string | null,
})
const dossier = {
  id: 'e1', display_name: 'Brandt, Hugo', normalized_text: 'hugo brandt', language: 'en', entity_type: 'PERSON', ner_type: 'PERSON',
  redirected_from: null, preferred_text: 'Brandt, Hugo', status: 'established' as const, ambiguous: false, note: null, hidden: false, claims: [], other_types: [],
  aliases: [], aliases_status: 'unavailable' as const, total_mentions: 9, article_count: 7, cluster_count: 1,
  first_seen_at: null, last_seen_at: null, timeline_days: 30, timeline: [],
}
const runs = (count: number) => ({ runs: Array.from({ length: count }, (_, index) => ({ id: `run-${index}`, kind: 'merge' as const, status: 'running' as const, entity_id: `x${index}`, root_id: 'e1' })) })

const open = (search = '') => {
  resetNavigationHarness({ pathname: '/authorities/', search })
  return renderWithQuery(() => <AuthoritiesPage />)
}
/** Follows the page's last URL change, as the router would. */
const follow = (view: ReturnType<typeof open>) => view.rerenderSame()

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.authorities).mockResolvedValue({
    items: [
      root('e1', 'Brandt, Hugo', { status: 'established', variant_count: 2, article_count: 7, qid: 'Q42' }),
      root('e2', 'Ines Alvar', { entity_type: 'ORG', language: 'el', article_count: 3, hidden: true }),
    ],
    next_cursor: null,
  })
  vi.mocked(api.authoritySuggestions).mockResolvedValue({ items: [suggestion()] })
  vi.mocked(api.authorityHistory).mockResolvedValue({ items: [], next_cursor: null })
  vi.mocked(api.mergeEntity).mockResolvedValue({ id: 'run', kind: 'merge', status: 'running', entity_id: 'v1', root_id: 'r1' })
  vi.mocked(api.addDistinct).mockResolvedValue(undefined)
  vi.mocked(api.wikidataCandidates).mockResolvedValue({ items: [review('e3', 'Kai Lund', 'Q7'), review('e4', 'Mara Ost', 'Q8')], next_cursor: 'more' })
  vi.mocked(api.entityDossier).mockResolvedValue(dossier)
  vi.mocked(api.entityVariants).mockResolvedValue({ items: [] })
  vi.mocked(api.entityHistory).mockResolvedValue({ items: [] })
  vi.mocked(api.seeAlso).mockResolvedValue({ labels: ['related'], items: [] })
  vi.mocked(api.wikidata).mockResolvedValue({
    entity_id: 'e1', qid: null, identifiers: {}, item: null, fetch_pending: false, names: [], candidates: [], search_pending: false, redirect_holder: null, wikidata_type: null,
  })
})
afterEach(cleanup)

it('splits the file into tabs with counts and keeps the tab in the URL', async () => {
  const view = open()

  const tabs = screen.getByRole('tablist', { name: 'Authority file sections' })
  await waitFor(() => expect(within(tabs).getAllByRole('tab').map(tab => tab.textContent)).toEqual(['Names', 'Maybe the same?1', 'Wikidata2+', 'Changes']))
  expect(within(tabs).getByRole('tab', { name: 'Names' })).toHaveAttribute('aria-selected', 'true')
  expect(screen.queryByRole('list', { name: 'Maybe the same?' })).toBeNull()

  fireEvent.click(within(tabs).getByRole('tab', { name: /^Maybe the same\?/ }))
  expect(navigationHarness.replace).toHaveBeenLastCalledWith('/authorities/?tab=suggestions')
  follow(view)
  expect(await screen.findByRole('list', { name: 'Maybe the same?' })).toBeTruthy()
  expect(screen.queryByRole('table', { name: 'Authority file' })).toBeNull()
})

it('lists the names as a table, most used first, with type, language, articles, other names, status and Wikidata', async () => {
  open()

  const table = await screen.findByRole('table', { name: 'Authority file' })
  const [head, ...rows] = within(table).getAllByRole('row')
  expect(within(head).getAllByRole('columnheader').map(cell => cell.textContent)).toEqual(['', 'Name', 'Type', 'Language', 'Articles', 'Other names', 'Status', 'Wikidata'])
  expect(rows.map(row => within(row).getAllByRole('cell').slice(1).map(cell => cell.textContent))).toEqual([
    ['Brandt, Hugo', 'Person', 'en', '7', '2', 'Established', 'Q42'],
    ['Ines AlvarHidden', 'Organization', 'el', '3', '0', 'Provisional', '—'],
  ])
  expect(within(rows[0]).getByRole('link', { name: 'Q42' })).toHaveAttribute('href', 'https://www.wikidata.org/wiki/Q42')
  expect(api.authorities).toHaveBeenCalledWith({ sort: 'articles' }, undefined)
})

it('filters by name, language, type, status, Wikidata and hidden, sorts, and loads more, all through the URL', async () => {
  vi.mocked(api.authorities).mockImplementation(async (_filters, cursor) => cursor
    ? { items: [root('e9', 'Zora Lim')], next_cursor: null }
    : { items: [root('e1', 'Brandt, Hugo')], next_cursor: 'next' })
  const view = open()

  fireEvent.click(await screen.findByRole('button', { name: 'Load more names' }))
  expect(await screen.findByRole('button', { name: 'Zora Lim' })).toBeTruthy()
  expect(api.authorities).toHaveBeenCalledWith({ sort: 'articles' }, 'next')

  fireEvent.change(screen.getByLabelText('Find a name'), { target: { value: 'hug' } })
  expect(navigationHarness.replace).toHaveBeenLastCalledWith('/authorities/?q=hug')
  follow(view)
  fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'el' } })
  follow(view)
  fireEvent.click(within(screen.getByRole('group', { name: /^Type/ })).getByLabelText('Topic'))
  follow(view)
  fireEvent.click(within(screen.getByRole('group', { name: /^Type/ })).getByLabelText('Organization'))
  follow(view)
  fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'provisional' } })
  follow(view)
  fireEvent.change(screen.getByLabelText('Wikidata'), { target: { value: 'unlinked' } })
  follow(view)
  fireEvent.change(screen.getByLabelText('Sort'), { target: { value: 'name' } })
  follow(view)
  fireEvent.click(screen.getByLabelText('Hidden names only'))
  follow(view)

  expect(navigationHarness.replace).toHaveBeenLastCalledWith('/authorities/?q=hug&language=el&status=provisional&wikidata=unlinked&type=TOPIC&type=ORG&sort=name&hidden=1')
  await waitFor(() => expect(api.authorities).toHaveBeenLastCalledWith(
    { q: 'hug', language: 'el', status: 'provisional', wikidata: 'unlinked', type: ['TOPIC', 'ORG'], sort: 'name', hidden: true }, undefined,
  ))
  expect(screen.getByRole('group', { name: 'Type (2)' })).toBeTruthy()
})

it('opens a name in the side panel and closes it again', async () => {
  const view = open()

  fireEvent.click(await screen.findByRole('button', { name: 'Brandt, Hugo' }))
  expect(navigationHarness.replace).toHaveBeenLastCalledWith('/authorities/?entity=e1')
  follow(view)
  const panel = await screen.findByRole('complementary', { name: 'Edit Brandt, Hugo' })
  expect(within(panel).getByRole('link', { name: 'Open dossier' })).toHaveAttribute('href', '/entities/?id=e1')
  expect(api.entityDossier).toHaveBeenCalledWith('e1', 30)
  expect(await within(panel).findByLabelText('Type')).toBeTruthy()

  fireEvent.click(within(panel).getByRole('button', { name: 'Close the editor' }))
  expect(navigationHarness.replace).toHaveBeenLastCalledWith('/authorities/')
})

it('changes the type, status and visibility of every selected name at once', async () => {
  vi.mocked(api.bulkAuthorities).mockResolvedValue(runs(2))
  open()

  fireEvent.click(await screen.findByLabelText('Select Brandt, Hugo'))
  fireEvent.click(screen.getByLabelText('Select Ines Alvar'))
  const bar = screen.getByRole('toolbar', { name: 'Bulk actions' })
  expect(within(bar).getByText('2 selected')).toBeTruthy()

  fireEvent.change(within(bar).getByLabelText('Set type to'), { target: { value: 'TOPIC' } })
  fireEvent.click(within(bar).getByRole('button', { name: 'Apply type' }))
  await waitFor(() => expect(api.bulkAuthorities).toHaveBeenLastCalledWith({ action: 'set_type', ids: ['e1', 'e2'], entity_type: 'TOPIC' }))
  expect(await screen.findByText('Changed 2 names.')).toBeTruthy()
  expect(screen.queryByRole('toolbar', { name: 'Bulk actions' })).toBeNull()
  await waitFor(() => expect(api.authorities).toHaveBeenCalledTimes(2))

  for (const [button, body] of [
    ['Mark established', { action: 'set_status', status: 'established' }],
    ['Mark provisional', { action: 'set_status', status: 'provisional' }],
    ['Hide', { action: 'hide' }],
    ['Show', { action: 'show' }],
  ] as const) {
    fireEvent.click(screen.getByLabelText('Select all shown'))
    fireEvent.click(within(screen.getByRole('toolbar', { name: 'Bulk actions' })).getByRole('button', { name: button }))
    await waitFor(() => expect(api.bulkAuthorities).toHaveBeenLastCalledWith({ ...body, ids: ['e1', 'e2'] }))
    await screen.findByText('Changed 2 names.')
  }
})

it('merges the selected names into the one kept, and explains a refusal', async () => {
  vi.mocked(api.bulkAuthorities).mockResolvedValueOnce(runs(1)).mockRejectedValueOnce(new ApiError('Ines Alvar: These entities are marked as different', 409))
  open()

  fireEvent.click(await screen.findByLabelText('Select Brandt, Hugo'))
  const bar = screen.getByRole('toolbar', { name: 'Bulk actions' })
  expect(within(bar).queryByRole('button', { name: /^Merge/ })).toBeNull()
  fireEvent.click(screen.getByLabelText('Select Ines Alvar'))
  fireEvent.change(within(bar).getByLabelText('Keep'), { target: { value: 'e1' } })
  fireEvent.click(within(bar).getByRole('button', { name: 'Merge 1 into Brandt, Hugo' }))
  await waitFor(() => expect(api.bulkAuthorities).toHaveBeenCalledWith({ action: 'merge', ids: ['e2'], target_id: 'e1' }))
  expect(await screen.findByText('1 name is merging into Brandt, Hugo; its articles move over shortly.')).toBeTruthy()

  fireEvent.click(screen.getByLabelText('Select Brandt, Hugo'))
  fireEvent.click(screen.getByLabelText('Select Ines Alvar'))
  fireEvent.change(screen.getByLabelText('Keep'), { target: { value: 'e2' } })
  fireEvent.click(screen.getByRole('button', { name: 'Merge 1 into Ines Alvar' }))
  expect((await screen.findByRole('alert')).textContent).toBe('Ines Alvar: These entities are marked as different')
  expect(screen.getByText('2 selected')).toBeTruthy()
})

it('shows each suggestion on a compact row with both types and article counts', async () => {
  open('tab=suggestions')

  const item = within(await screen.findByRole('list', { name: 'Maybe the same?' })).getByRole('listitem')
  expect(within(item).getByText('Is “J. Tarr” the same as “Jon Tarr”?')).toBeTruthy()
  expect(within(item).getByText('Initials · shared articles · 1 article together')).toBeTruthy()
  expect(within(item).getByText('Person · 1 article')).toBeTruthy()
  expect(within(item).getByText('Organization · 4 articles')).toBeTruthy()
  expect(within(item).getByRole('link', { name: 'J. Tarr' })).toHaveAttribute('href', '/entities/?id=v1')
  expect(within(item).getByRole('link', { name: 'Jon Tarr' })).toHaveAttribute('href', '/entities/?id=r1')
  expect(api.authoritySuggestions).toHaveBeenCalledWith(undefined)
})

it('asks for suggestions in the language chosen on the Names tab', async () => {
  open('tab=suggestions&language=el')
  await screen.findByRole('list', { name: 'Maybe the same?' })
  expect(api.authoritySuggestions).toHaveBeenCalledWith('el')
})

it('approves a suggestion by merging the variant into the root, and refreshes the queue', async () => {
  open('tab=suggestions')

  fireEvent.click(await screen.findByRole('button', { name: 'Same entity: merge J. Tarr into Jon Tarr' }))
  await waitFor(() => expect(api.mergeEntity).toHaveBeenCalledWith('v1', 'r1'))
  expect(await screen.findByText('J. Tarr is now a name of Jon Tarr; its articles move over shortly.')).toBeTruthy()
  await waitFor(() => expect(api.authoritySuggestions).toHaveBeenCalledTimes(2))
})

it('can keep the other name instead', async () => {
  open('tab=suggestions')

  fireEvent.click(await screen.findByRole('button', { name: 'Same entity, keep J. Tarr: merge Jon Tarr into J. Tarr' }))
  await waitFor(() => expect(api.mergeEntity).toHaveBeenCalledWith('r1', 'v1'))
  expect(await screen.findByText('Jon Tarr is now a name of J. Tarr; its articles move over shortly.')).toBeTruthy()
})

it('rejects a suggestion by recording the pair as different', async () => {
  open('tab=suggestions')

  fireEvent.click(await screen.findByRole('button', { name: 'Different: keep J. Tarr apart from Jon Tarr' }))
  await waitFor(() => expect(api.addDistinct).toHaveBeenCalledWith('r1', 'v1'))
  expect(await screen.findByText('J. Tarr and Jon Tarr are recorded as different.')).toBeTruthy()
  await waitFor(() => expect(api.authoritySuggestions).toHaveBeenCalledTimes(2))
})

it('explains a refused decision and an empty queue', async () => {
  vi.mocked(api.mergeEntity).mockRejectedValue(new ApiError('These entities are marked as different', 409))
  open('tab=suggestions')

  fireEvent.click(await screen.findByRole('button', { name: 'Same entity: merge J. Tarr into Jon Tarr' }))
  expect((await screen.findByRole('alert')).textContent).toBe('These entities are marked as different')

  cleanup()
  vi.mocked(api.authoritySuggestions).mockResolvedValue({ items: [] })
  open('tab=suggestions')
  expect(await screen.findByText('No likely duplicates right now.')).toBeTruthy()
})

it('shows recent changes newest first, linked to both entities', async () => {
  vi.mocked(api.authorityHistory)
    .mockResolvedValueOnce({ items: [change('h2', 'renamed', 'Lund, Kai', { preferred_text: 'Lund, Kai' }), change('h1', 'status_changed', 'Lund, Kai', { status: 'established' })], next_cursor: 'older' })
    .mockResolvedValueOnce({ items: [{ ...change('h0', 'merged', 'NAC'), other_id: 'r9', other_name: 'North Atlantic Council' }, change('hx', 'ambiguous_changed', null, { ambiguous: true }), { ...change('hr', 'relation_added', 'Kai Rho'), other_id: 'u1', other_name: 'Union Party' }, { ...change('hq', 'relation_removed', 'Kai Rho'), other_id: 'u1', other_name: 'Union Party' }], next_cursor: null })
  open('tab=changes')

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
  expect(within(list).getAllByRole('listitem')[4].textContent).toContain('Kai Rho linked to Union Party')
  expect(within(list).getAllByRole('listitem')[5].textContent).toContain('Kai Rho no longer linked to Union Party')
  expect(api.authorityHistory).toHaveBeenLastCalledWith('older')
})

it('describes type, hidden and claim changes in the history', async () => {
  vi.mocked(api.authorityHistory).mockResolvedValue({
    items: [
      { ...change('t1', 'type_changed', 'AI', { entity_type: 'TOPIC' }), before: { entity_type: 'ORG' } },
      { ...change('t2', 'hidden_changed', 'Monday', { hidden: true }), before: { hidden: false } },
      { ...change('t3', 'hidden_changed', 'Monday', { hidden: false }), before: { hidden: true } },
      change('t4', 'claim_added', 'AI', { normalized_text: 'ai' }),
      { ...change('t5', 'claim_removed', 'AI'), before: { normalized_text: 'ai' } },
      { ...change('t6', 'merged', 'AI', { authority_id: 'r1', by: 'claim' }), other_id: 'r1', other_name: 'Artificial intelligence' },
    ],
    next_cursor: null,
  })
  open('tab=changes')

  const rows = within(await screen.findByRole('list', { name: 'Recent changes' })).getAllByRole('listitem')
  expect(rows.map(row => row.textContent?.split(' · ')[0])).toEqual([
    'AI: type changed from Organization to Topic',
    'Monday: hidden as not a name',
    'Monday: shown again',
    'AI: claims every type of “ai”',
    'AI: no longer claims “ai”',
    'AI joined Artificial intelligence by its claimed name',
  ])
})

it('describes the Wikidata changes in the history', async () => {
  vi.mocked(api.authorityHistory).mockResolvedValue({
    items: [
      { ...change('w1', 'wikidata_linked', 'Kai Lund', { qid: 'Q7' }) },
      { ...change('w2', 'wikidata_unlinked', 'Kai Lund'), before: { qid: 'Q7' } },
      { ...change('w3', 'wikidata_redirected', 'Kai Lund', { qid: 'Q9' }), before: { qid: 'Q7' } },
      { ...change('w4', 'wikidata_missing', 'Kai Lund'), before: { qid: 'Q9' } },
    ],
    next_cursor: null,
  })
  open('tab=changes')

  const rows = within(await screen.findByRole('list', { name: 'Recent changes' })).getAllByRole('listitem')
  expect(rows.map(row => row.textContent?.split(' · ')[0])).toEqual([
    'Kai Lund: linked to Wikidata Q7',
    'Kai Lund: unlinked from Wikidata Q7',
    'Kai Lund: Wikidata merged Q7 into Q9; the link moved',
    'Kai Lund: Wikidata deleted Q9; the link is kept',
  ])
})

it('reviews the Wikidata suggestions: link one, dismiss one, load more', async () => {
  vi.mocked(api.wikidataCandidates).mockImplementation(async cursor => cursor
    ? { items: [review('e5', 'Ola Brun', 'Q9')], next_cursor: null }
    : { items: [review('e3', 'Kai Lund', 'Q7'), review('e4', 'Mara Ost', 'Q8', { exact: false, reasons: ['alias:en'] })], next_cursor: 'more' })
  vi.mocked(api.linkWikidata).mockResolvedValue({} as never)
  vi.mocked(api.dismissWikidataCandidate).mockResolvedValue(undefined)
  open('tab=wikidata')

  const queue = await screen.findByRole('list', { name: 'Wikidata suggestions' })
  const rows = within(queue).getAllByRole('listitem')
  expect(within(rows[0]).getByRole('link', { name: 'Kai Lund' })).toHaveAttribute('href', '/entities/?id=e3')
  expect(within(rows[0]).getByRole('link', { name: 'Q7' })).toHaveAttribute('href', 'https://www.wikidata.org/wiki/Q7')
  expect(within(rows[0]).getByText('Kai Lund · Danish runner')).toBeTruthy()
  expect(within(rows[0]).getByText('Exact label (en) · Type matches')).toBeTruthy()
  expect(within(rows[1]).getByText('Exact alias (en)')).toBeTruthy()

  fireEvent.click(within(rows[0]).getByRole('button', { name: 'Link Kai Lund to Q7' }))
  await waitFor(() => expect(api.linkWikidata).toHaveBeenCalledWith('e3', 'Q7', []))
  expect(await screen.findByText('Kai Lund is linked to Q7.')).toBeTruthy()

  fireEvent.click(await screen.findByRole('button', { name: 'Mara Ost is not Q8' }))
  await waitFor(() => expect(api.dismissWikidataCandidate).toHaveBeenCalledWith('e4', 'Q8'))

  expect(await screen.findByText('Mara Ost is not Q8.')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Load more suggestions' }))
  expect(await screen.findByRole('link', { name: 'Ola Brun' })).toBeTruthy()
  expect(api.wikidataCandidates).toHaveBeenCalledWith('more')
})

it('approves every exact Wikidata match at once and lists what it skipped', async () => {
  vi.mocked(api.wikidataCandidates).mockResolvedValue({ items: [review('e3', 'Kai Lund', 'Q7')], next_cursor: null })
  vi.mocked(api.approveExactWikidata).mockResolvedValue({ linked: 3, skipped: [{ entity_id: 'e4', qid: 'Q8', message: 'Q8 is already linked to Mara Ost' }] })
  open('tab=wikidata')

  await screen.findByRole('list', { name: 'Wikidata suggestions' })
  fireEvent.click(screen.getByRole('button', { name: 'Approve all exact' }))
  await waitFor(() => expect(api.approveExactWikidata).toHaveBeenCalled())
  expect(await screen.findByText('Linked 3 entities; 1 skipped.')).toBeTruthy()
  const skipped = screen.getByRole('list', { name: 'Skipped' })
  expect(within(skipped).getByText('Q8 is already linked to Mara Ost')).toBeTruthy()
  await waitFor(() => expect(api.wikidataCandidates).toHaveBeenCalledTimes(2))

  cleanup()
  vi.mocked(api.wikidataCandidates).mockResolvedValue({ items: [], next_cursor: null })
  open('tab=wikidata')
  expect(await screen.findByText('No Wikidata suggestions to review.')).toBeTruthy()
})

it('says so when the file cannot be loaded', async () => {
  vi.mocked(api.authorities).mockRejectedValue(new Error('down'))
  vi.mocked(api.authoritySuggestions).mockRejectedValue(new Error('down'))
  open()
  expect(await screen.findByText('Could not load the authority file.')).toBeTruthy()

  cleanup()
  open('tab=suggestions')
  expect(await screen.findByText('Could not load suggestions.')).toBeTruthy()
})

it('merges from the side panel and opens the name kept there, staying on the file', async () => {
  vi.mocked(api.nlpEntities).mockResolvedValue({ items: [{ id: 'e9', kind: 'PERSON', normalized_text: 'hugo brandt jr', text: 'Hugo Brandt Jr' }], next_cursor: null })
  vi.mocked(api.mergeEntity).mockResolvedValue({ id: 'run', kind: 'merge', status: 'running', entity_id: 'e1', root_id: 'e9' })
  open('entity=e1')

  const panel = await screen.findByRole('complementary', { name: 'Edit Brandt, Hugo' })
  fireEvent.click(within(panel).getByRole('button', { name: 'Merge with…' }))
  fireEvent.change(within(panel).getByLabelText('Find the entity to merge into'), { target: { value: 'hugo' } })
  fireEvent.click(await within(panel).findByRole('button', { name: 'Hugo Brandt Jr (PERSON)' }))
  fireEvent.click(within(panel).getByRole('button', { name: 'Merge into Hugo Brandt Jr' }))

  await waitFor(() => expect(navigationHarness.replace).toHaveBeenLastCalledWith('/authorities/?entity=e9'))
  expect(navigationHarness.push).not.toHaveBeenCalled()
})
