import { cleanup, fireEvent, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../../lib/api'
import type { MonitorResult, MonitorResultPage } from '../../lib/api-types'
import { navigationHarness, resetNavigationHarness } from '../../test/navigation-harness'
import { changes, monitor } from '../../test/monitors'
import { renderWithQuery } from '../../test/render'
import MonitorsPage from './page'

vi.mock('../../lib/api', async importOriginal => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: { monitors: vi.fn(), monitor: vi.fn(), monitorResults: vi.fn(), monitorChanges: vi.fn(), updateMonitor: vi.fn(), markMonitorViewed: vi.fn(), deleteMonitor: vi.fn() },
}))

const NOW = Date.parse('2026-09-20T12:00:00Z')
const result = (id: string, title: string, overrides: Partial<MonitorResult> = {}): MonitorResult => ({
  article_id: id, title, effective_date: '2026-09-20T11:50:00Z', distinct_source_count: 1, sources: ['Wire'],
  source_refs: [{ id: 's1', name: 'Wire', country: 'GR' }], story_country: null, story_cluster: null, summary: null, highlights: [], entities: [], read: false, starred: false, ...overrides,
})
const page = (items: MonitorResult[], overrides: Partial<MonitorResultPage> = {}): MonitorResultPage => ({
  items, next_cursor: null, window_start: '2026-09-20T11:00:00Z', window_end: '2026-09-20T11:55:00Z', ...overrides,
})
const unseen = { unseen_article_count: 2, unseen_cluster_count: 1 }

beforeEach(() => {
  vi.clearAllMocks()
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
  resetNavigationHarness({ pathname: '/monitors/' })
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals() })

// The button stays disabled until the results (and so the boundary being marked) have loaded.
const markButton = async () => {
  const button = await screen.findByRole('button', { name: 'Mark as seen (2)' })
  await vi.waitFor(() => expect(button).toBeEnabled())
  return button
}

it('sends the old watchlist address to Investigations, keeping the order', async () => {
  resetNavigationHarness({ pathname: '/monitors/', search: 'order=name' })
  renderWithQuery(() => <MonitorsPage />)

  await vi.waitFor(() => expect(navigationHarness.replace).toHaveBeenCalledWith('/investigations/?order=name'))
})

// ---- detail (?id=) ----

const openDetail = (search = 'id=m1') => resetNavigationHarness({ pathname: '/monitors/', search })
const arrange = (item = monitor({ id: 'm1', name: 'Harbor watch', ...unseen }), unseenPage = page([result('a1', 'Harbor strike widens'), result('a2', 'Port talks resume')])) => {
  openDetail()
  vi.mocked(api.monitor).mockResolvedValue(item)
  vi.mocked(api.monitorResults).mockImplementation(async (_id, scope) => scope === 'unseen' ? unseenPage : page([result('a0', 'Older harbor story')]))
  vi.mocked(api.monitorChanges).mockResolvedValue(changes())
}

it('shows the counters, what they cover and the unseen articles, linking back to this view', async () => {
  arrange()
  renderWithQuery(() => <MonitorsPage />)

  expect(await screen.findByRole('heading', { level: 3, name: 'Harbor watch' })).toBeTruthy()
  expect(screen.getByText('2 new articles · 1 new story')).toBeTruthy()
  expect(screen.getAllByText(/Counted through/).length).toBeGreaterThan(0) // the header, and the changes panel once it loads
  expect(screen.getByRole('link', { name: 'Open in search' })).toHaveAttribute('href', '/search/?q=harbor')
  expect(screen.getByRole('link', { name: 'Edit criteria' })).toHaveAttribute('href', '/search/?q=harbor&monitor=m1')
  expect(await screen.findByRole('link', { name: 'Harbor strike widens' })).toHaveAttribute('href', '/articles/?article=a1&from=%2Fmonitors%2F%3Fid%3Dm1')
  expect(api.monitorResults).toHaveBeenCalledWith('m1', 'unseen', undefined)
  expect(screen.getAllByRole('link', { name: 'Wire' })[0].getAttribute('href')).toMatch(/^\/sources\/detail\/\?id=s1&from=%2Fmonitors%2F/)
})

it('reports a monitor that does not exist', async () => {
  arrange()
  vi.mocked(api.monitor).mockRejectedValue(new ApiError('Monitor not found', 404))
  renderWithQuery(() => <MonitorsPage />)

  expect(await screen.findByText('Monitor not found.')).toBeTruthy()
})

it('keeps the scope in the URL and lists recent matches under it', async () => {
  arrange()
  const view = renderWithQuery(() => <MonitorsPage />)
  await screen.findByRole('link', { name: 'Harbor strike widens' })

  await fireEvent.click(screen.getByRole('button', { name: 'Recent matches' }))
  expect(navigationHarness.push).toHaveBeenCalledWith('/monitors/?id=m1&scope=recent')
  view.rerenderSame()

  expect(await screen.findByRole('link', { name: 'Older harbor story' })).toBeTruthy()
  expect(screen.queryByRole('button', { name: /Mark as seen/ })).toBeNull()
})

it('marks as seen up to the boundary of the results on screen, then shows the emptied list', async () => {
  arrange()
  vi.mocked(api.markMonitorViewed).mockImplementation(async () => {
    vi.mocked(api.monitor).mockResolvedValue(monitor({ id: 'm1', name: 'Harbor watch' }))
    vi.mocked(api.monitorResults).mockResolvedValue(page([], { window_start: '2026-09-20T11:55:00Z' }))
    return monitor({ id: 'm1', name: 'Harbor watch' })
  })
  renderWithQuery(() => <MonitorsPage />)

  await fireEvent.click(await markButton())

  await vi.waitFor(() => expect(api.markMonitorViewed).toHaveBeenCalledWith('m1', '2026-09-20T11:55:00Z'))
  expect(await screen.findByText('Nothing new since you last looked.')).toBeTruthy()
  expect(screen.getByText('Nothing new')).toBeTruthy()
})

it('does not offer to mark anything seen when nothing is unseen', async () => {
  arrange(monitor({ id: 'm1', name: 'Harbor watch' }), page([], { window_end: null }))
  renderWithQuery(() => <MonitorsPage />)

  await screen.findByRole('heading', { level: 3, name: 'Harbor watch' })
  expect(screen.getByRole('button', { name: /Mark as seen/ })).toBeDisabled()
})

it('never reads an empty list as "nothing new" while a recount is due', async () => {
  arrange(monitor({ id: 'm1', name: 'Harbor watch', next_evaluation_at: '2026-09-20T11:59:59Z' }), page([]))
  renderWithQuery(() => <MonitorsPage />)

  expect(await screen.findByText('Checking for newer articles…')).toBeTruthy()
  expect(screen.queryByText('Nothing new since you last looked.')).toBeNull()
})

it('surfaces a refused mark-as-seen', async () => {
  arrange()
  vi.mocked(api.markMonitorViewed).mockRejectedValue(new ApiError('This monitor has not been evaluated yet', 409))
  renderWithQuery(() => <MonitorsPage />)

  await fireEvent.click(await markButton())

  expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'This monitor has not been evaluated yet')
})

it('explains a monitor whose state cannot be read and returns no results for it', async () => {
  arrange(monitor({ id: 'm1', name: 'Broken', state: null, problem: 'state.retired_filter: Extra inputs are not permitted' }))
  vi.mocked(api.monitorResults).mockRejectedValue(new ApiError('invalid', 409))
  renderWithQuery(() => <MonitorsPage />)

  expect(await screen.findByText(/can no longer be evaluated: state.retired_filter/)).toBeTruthy()
  expect(screen.queryByRole('link', { name: 'Open in search' })).toBeNull()
  expect(screen.queryByRole('link', { name: 'Edit criteria' })).toBeNull()
  expect(screen.getByRole('button', { name: 'Rename monitor' })).toBeTruthy()
})

it('renames, reporting a duplicate name first', async () => {
  arrange()
  vi.mocked(api.updateMonitor).mockRejectedValueOnce(new ApiError('A monitor with this name already exists', 409)).mockResolvedValueOnce(monitor({ id: 'm1', name: 'Port watch' }))
  renderWithQuery(() => <MonitorsPage />)

  await fireEvent.click(await screen.findByRole('button', { name: 'Rename monitor' }))
  fireEvent.change(screen.getByLabelText('New name for Harbor watch'), { target: { value: 'Grid' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Save name' }))
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'A monitor with this name already exists')

  fireEvent.change(screen.getByLabelText('New name for Harbor watch'), { target: { value: ' Port watch ' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Save name' }))

  await vi.waitFor(() => expect(api.updateMonitor).toHaveBeenLastCalledWith('m1', { name: 'Port watch' }))
})

it('pauses from the detail and goes back to Investigations after a confirmed delete', async () => {
  arrange()
  vi.stubGlobal('confirm', vi.fn().mockReturnValue(true))
  vi.mocked(api.updateMonitor).mockResolvedValue(monitor({ id: 'm1', enabled: false }))
  vi.mocked(api.deleteMonitor).mockResolvedValue(undefined)
  renderWithQuery(() => <MonitorsPage />)

  await fireEvent.click(await screen.findByRole('button', { name: 'Pause monitor' }))
  await vi.waitFor(() => expect(api.updateMonitor).toHaveBeenCalledWith('m1', { enabled: false }))
  await fireEvent.click(screen.getByRole('button', { name: 'Delete monitor' }))

  await vi.waitFor(() => expect(navigationHarness.push).toHaveBeenLastCalledWith('/investigations/'))
  expect(api.deleteMonitor).toHaveBeenCalledWith('m1')
})

// ---- what changed ----

const evidence = [{ article_id: 'a1', title: 'Harbor strike widens' }]
const populated = changes({
  article_count: 2,
  sources: [{ source_id: 's9', name: 'Harbor Wire', article_count: 2, evidence }],
  entities: [{ entity_id: 'e1', name: 'Acme', entity_type: 'ORG', article_count: 1, evidence }],
  stories: [{ cluster_id: 'c1', title: 'Port strike', status: 'grew', article_count: 1, source_count: 5, sources_added: 2, evidence }],
})

it('lists what changed with a link to the subject and to the evidence articles', async () => {
  arrange()
  vi.mocked(api.monitorChanges).mockResolvedValue(populated)
  renderWithQuery(() => <MonitorsPage />)

  const panel = await screen.findByRole('region', { name: 'What changed' })
  expect(await within(panel).findByRole('link', { name: 'New source: Harbor Wire (2 articles)' })).toHaveAttribute('href', '/search/?q=harbor&source_id=s9')
  expect(within(panel).getByText('2 new articles')).toBeTruthy()
  expect(within(panel).getByRole('link', { name: 'New entity: Acme (ORG) — 1 article' })).toHaveAttribute('href', '/entities/?id=e1')
  expect(within(panel).getByRole('link', { name: 'Story grew: Port strike — 3 → 5 sources (+2)' })).toHaveAttribute('href', '/clusters/?id=c1&from=%2Fmonitors%2F%3Fid%3Dm1')
  expect(within(panel).getAllByRole('link', { name: 'Harbor strike widens' })[0]).toHaveAttribute('href', '/articles/?article=a1&from=%2Fmonitors%2F%3Fid%3Dm1')
  expect(api.monitorChanges).toHaveBeenCalledWith('m1')
})

it('is not shown for the recent scope, which has no boundary to compare with', async () => {
  arrange()
  openDetail('id=m1&scope=recent')
  renderWithQuery(() => <MonitorsPage />)

  await screen.findByRole('link', { name: 'Older harbor story' })
  expect(screen.queryByRole('region', { name: 'What changed' })).toBeNull()
})

it('says so when nothing changed, but never during a recount', async () => {
  arrange()
  const view = renderWithQuery(() => <MonitorsPage />)
  expect(await screen.findByText('No changes since you last looked.')).toBeTruthy()
  view.unmount()

  arrange(monitor({ id: 'm1', name: 'Harbor watch', next_evaluation_at: '2026-09-20T11:59:59Z' }))
  renderWithQuery(() => <MonitorsPage />)
  expect(await screen.findByText('The changes are being refreshed.')).toBeTruthy()
  expect(screen.queryByText('No changes since you last looked.')).toBeNull()
})

it('reports a failed load and refetches once the counters move', async () => {
  arrange()
  vi.mocked(api.monitorChanges).mockRejectedValueOnce(new ApiError('boom', 500)).mockResolvedValue(populated)
  vi.mocked(api.markMonitorViewed).mockImplementation(async () => {
    vi.mocked(api.monitor).mockResolvedValue(monitor({ id: 'm1', name: 'Harbor watch' }))
    vi.mocked(api.monitorChanges).mockResolvedValue(changes())
    return monitor({ id: 'm1', name: 'Harbor watch' })
  })
  renderWithQuery(() => <MonitorsPage />)
  expect(await screen.findByText('Could not load the changes.')).toBeTruthy()

  await fireEvent.click(await markButton())

  expect(await screen.findByText('No changes since you last looked.')).toBeTruthy()
})
