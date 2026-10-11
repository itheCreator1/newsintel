import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../lib/api'
import { resetNavigationHarness } from '../test/navigation-harness'
import { renderWithQuery } from '../test/render'
import { AuthorityPanel } from './AuthorityPanel'

vi.mock('../lib/api', async importOriginal => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: {
    entityDossier: vi.fn(), entityVariants: vi.fn(), entityHistory: vi.fn(), updateEntity: vi.fn(), mergeEntity: vi.fn(), splitEntity: vi.fn(),
    addDistinct: vi.fn(), nlpEntities: vi.fn(), wikidata: vi.fn(), claimName: vi.fn(), releaseClaim: vi.fn(), seeAlso: vi.fn(),
  },
}))

const dossier = (over = {}) => ({
  id: 'ent-1', display_name: 'AI', normalized_text: 'ai', language: 'en', entity_type: 'ORG', ner_type: 'ORG',
  redirected_from: null, preferred_text: null, status: 'provisional' as const, ambiguous: false, note: null, hidden: false,
  claims: [] as string[], other_types: [] as { id: string; display_name: string; entity_type: string; normalized_text: string }[],
  aliases: [], aliases_status: 'unavailable' as const, total_mentions: 30, article_count: 12, cluster_count: 2,
  first_seen_at: null, last_seen_at: null, timeline_days: 30, timeline: [], ...over,
})
const wikidata = (over = {}) => ({
  entity_id: 'ent-1', qid: null as string | null, identifiers: {}, item: null, fetch_pending: false, names: [], candidates: [], search_pending: false,
  redirect_holder: null, wikidata_type: null as string | null, ...over,
})
const authority = { id: 'ent-1', display_name: 'AI', preferred_text: null, entity_type: 'ORG', ner_type: 'ORG', hidden: false, status: 'provisional' as const, ambiguous: false, note: null, authority_id: null }
const change = (id: string, action: string, before: Record<string, unknown> | null, after: Record<string, unknown> | null) => ({
  id, action, entity_id: 'ent-1', other_id: null, before, after, created_at: '2026-10-11T10:00:00Z',
})

beforeEach(() => {
  vi.clearAllMocks()
  resetNavigationHarness({ pathname: '/authorities/' })
  vi.mocked(api.entityDossier).mockResolvedValue(dossier())
  vi.mocked(api.entityVariants).mockResolvedValue({ items: [] })
  vi.mocked(api.entityHistory).mockResolvedValue({ items: [] })
  vi.mocked(api.wikidata).mockResolvedValue(wikidata())
  vi.mocked(api.seeAlso).mockResolvedValue({ labels: ['related'], items: [] })
  vi.mocked(api.updateEntity).mockResolvedValue(authority)
})
afterEach(cleanup)

it('loads the root and names what it is, with a way to its dossier and out', async () => {
  const onClose = vi.fn()
  renderWithQuery(() => <AuthorityPanel entityId="ent-1" onClose={onClose} />)

  const panel = await screen.findByRole('complementary', { name: 'Edit AI' })
  expect(within(panel).getByRole('heading', { name: 'AI' })).toBeTruthy()
  expect(within(panel).getByText('Organization · en · 12 articles')).toBeTruthy()
  expect(within(panel).getByRole('link', { name: 'Open dossier' })).toHaveAttribute('href', '/entities/?id=ent-1')
  expect(api.entityDossier).toHaveBeenCalledWith('ent-1', 30)
  fireEvent.click(within(panel).getByRole('button', { name: 'Close the editor' }))
  expect(onClose).toHaveBeenCalled()
})

it('changes the type, TOPIC included, and says what the articles called it', async () => {
  vi.mocked(api.entityDossier).mockResolvedValue(dossier({ entity_type: 'PERSON', ner_type: 'ORG' }))
  renderWithQuery(() => <AuthorityPanel entityId="ent-1" />)

  const type = await screen.findByLabelText('Type') as HTMLSelectElement
  expect(type.value).toBe('PERSON')
  expect(Array.from(type.options).map(option => option.textContent)).toEqual(['Person', 'Organization', 'Country or city', 'Place', 'Event', 'Product', 'Topic', 'Other'])
  expect(screen.getByText('The articles call it Organization.')).toBeTruthy()

  fireEvent.change(type, { target: { value: 'TOPIC' } })
  await waitFor(() => expect(api.updateEntity).toHaveBeenCalledWith('ent-1', { entity_type: 'TOPIC' }))
  expect(await screen.findByText('AI is now a Topic; search and the graph follow shortly.')).toBeTruthy()
  await waitFor(() => expect(api.entityDossier).toHaveBeenCalledTimes(2))
})

it('offers the type Wikidata gives when it differs, and changes nothing by itself', async () => {
  vi.mocked(api.wikidata).mockResolvedValue(wikidata({ qid: 'Q11660', wikidata_type: 'PERSON' }))
  renderWithQuery(() => <AuthorityPanel entityId="ent-1" />)

  expect(await screen.findByText('Wikidata Q11660 says this is a Person.')).toBeTruthy()
  expect(api.updateEntity).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Change type to Person' }))
  await waitFor(() => expect(api.updateEntity).toHaveBeenCalledWith('ent-1', { entity_type: 'PERSON' }))

  cleanup()
  vi.mocked(api.wikidata).mockResolvedValue(wikidata({ qid: 'Q11660', wikidata_type: 'ORG' }))
  renderWithQuery(() => <AuthorityPanel entityId="ent-1" />)
  await screen.findByLabelText('Type')
  await waitFor(() => expect(api.wikidata).toHaveBeenCalled())
  expect(screen.queryByText(/Wikidata Q11660 says/)).toBeNull()
})

it('lists the same name under other types and brings every type of it here', async () => {
  vi.mocked(api.entityDossier).mockResolvedValue(dossier({
    other_types: [
      { id: 'p1', display_name: 'AI', entity_type: 'PERSON', normalized_text: 'ai' },
      { id: 'p2', display_name: 'Ai', entity_type: 'PRODUCT', normalized_text: 'ai' },
      { id: 'p3', display_name: 'A.I.', entity_type: 'OTHER', normalized_text: 'a.i.' },
    ],
  }))
  vi.mocked(api.claimName).mockResolvedValue({
    runs: [{ id: 'r1', kind: 'merge', status: 'running', entity_id: 'p1', root_id: 'ent-1' }],
    skipped: [{ entity_id: 'p2', display_name: 'Ai', entity_type: 'PRODUCT', reason: 'These entities are marked as different' }],
  })
  renderWithQuery(() => <AuthorityPanel entityId="ent-1" />)

  const others = await screen.findByRole('list', { name: 'Same name, other types' })
  expect(within(others).getAllByRole('listitem').map(item => item.textContent)).toEqual(['AI · Person', 'Ai · Product', 'A.I. · Other'])
  expect(within(others).getByRole('link', { name: 'AI' })).toHaveAttribute('href', '/entities/?id=p1')

  fireEvent.click(screen.getByRole('button', { name: 'Bring every type of “AI” here' }))
  await waitFor(() => expect(api.claimName).toHaveBeenCalledWith('ent-1', 'ai'))
  expect(await screen.findByText('Claimed “AI”: 1 name joins shortly, and every “AI” found later joins too.')).toBeTruthy()
  const skipped = screen.getByRole('list', { name: 'Not brought here' })
  expect(within(skipped).getByText('Ai (Product): These entities are marked as different')).toBeTruthy()

  expect(screen.getByRole('button', { name: 'Bring every type of “A.I.” here' })).toBeTruthy()
})

it('shows the claimed names and stops a claim', async () => {
  vi.mocked(api.entityDossier).mockResolvedValue(dossier({ claims: ['ai'] }))
  vi.mocked(api.releaseClaim).mockResolvedValue(undefined)
  renderWithQuery(() => <AuthorityPanel entityId="ent-1" />)

  const claims = await screen.findByRole('list', { name: 'Claimed names' })
  expect(within(claims).getByText('“ai”: every type joins here')).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Bring every type of “AI” here' })).toBeNull()
  fireEvent.click(within(claims).getByRole('button', { name: 'Stop claiming “ai”' }))
  await waitFor(() => expect(api.releaseClaim).toHaveBeenCalledWith('ent-1', 'ai'))
})

it('explains a refused claim', async () => {
  vi.mocked(api.claimName).mockRejectedValue(new ApiError('Another entity claims this name', 409))
  renderWithQuery(() => <AuthorityPanel entityId="ent-1" />)

  fireEvent.click(await screen.findByRole('button', { name: 'Bring every type of “AI” here' }))
  expect((await screen.findByRole('alert')).textContent).toBe('Another entity claims this name')
})

it('hides a root that is not a name and shows it again', async () => {
  renderWithQuery(() => <AuthorityPanel entityId="ent-1" />)

  fireEvent.click(await screen.findByRole('button', { name: 'Hide as not a name' }))
  await waitFor(() => expect(api.updateEntity).toHaveBeenCalledWith('ent-1', { hidden: true }))

  cleanup()
  vi.mocked(api.entityDossier).mockResolvedValue(dossier({ hidden: true }))
  renderWithQuery(() => <AuthorityPanel entityId="ent-1" />)
  expect(await screen.findByText('Hidden: left out of search, the graph and the suggestions.')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Show again' }))
  await waitFor(() => expect(api.updateEntity).toHaveBeenCalledWith('ent-1', { hidden: false }))
})

it('keeps the history folded and describes the new changes', async () => {
  vi.mocked(api.entityHistory).mockResolvedValue({ items: [
    change('h1', 'type_changed', { entity_type: 'ORG' }, { entity_type: 'TOPIC' }),
    change('h2', 'hidden_changed', { hidden: false }, { hidden: true }),
    change('h3', 'hidden_changed', { hidden: true }, { hidden: false }),
    change('h4', 'claim_added', null, { normalized_text: 'ai' }),
    change('h5', 'claim_removed', { normalized_text: 'ai' }, null),
    { ...change('h6', 'merged', { authority_id: null }, { authority_id: 'ent-1', by: 'claim' }), entity_id: 'p9', other_id: 'ent-1' },
  ] })
  renderWithQuery(() => <AuthorityPanel entityId="ent-1" />)

  const history = await screen.findByRole('list', { name: 'Authority history' })
  expect(history.closest('details')).not.toHaveAttribute('open')
  expect(within(history).getAllByRole('listitem').map(item => item.textContent?.split(' · ')[0])).toEqual([
    'Type changed from Organization to Topic',
    'Hidden as not a name',
    'Shown again',
    'Claimed every type of “ai”',
    'Stopped claiming “ai”',
    'A name found under another type joined by its claim',
  ])
})

it('keeps the see-also and Wikidata sections, which the dossier page shows by itself', async () => {
  renderWithQuery(() => <AuthorityPanel entityId="ent-1" />)
  expect(await screen.findByRole('heading', { name: 'See also' })).toBeTruthy()
  expect(screen.getByRole('heading', { name: 'Wikidata' })).toBeTruthy()

  cleanup()
  vi.mocked(api.entityDossier).mockClear()
  renderWithQuery(() => <AuthorityPanel entityId="ent-1" entity={dossier()} onDossier />)
  await screen.findByLabelText('Type')
  expect(screen.queryByRole('heading', { name: 'See also' })).toBeNull()
  expect(screen.queryByRole('link', { name: 'Open dossier' })).toBeNull()
  expect(api.entityDossier).not.toHaveBeenCalled()
})
