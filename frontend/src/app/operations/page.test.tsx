import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '../../lib/api'
import type { OpsFailures, OpsFeeds, OpsHealth, OpsPipelines, OpsStorage } from '../../lib/api-types'
import { navigationHarness, resetNavigationHarness } from '../../test/navigation-harness'
import { renderWithQuery } from '../../test/render'
import OperationsPage from './page'

vi.mock('../../lib/api', async importOriginal => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: { opsHealth: vi.fn(), opsPipelines: vi.fn(), opsFeeds: vi.fn(), opsStorage: vi.fn(), opsFailures: vi.fn() },
}))

const AT = '2026-09-21T10:00:00Z'
const probe = (name: string, state: string, over = {}) => ({ name, state, latency_ms: 3, detail: null, checked_at: AT, ...over })
const health = (over = {}) => ({
  generated_at: AT,
  probes: [
    probe('postgres', 'ok'), probe('redis', 'ok'),
    probe('elasticsearch', 'down', { latency_ms: null, detail: 'ConnectError' }),
    probe('nlp', 'ok', { detail: 'entities disabled by configuration' }),
    probe('scheduler', 'ok', { detail: 'Last cycle 4 s ago' }),
  ],
  queues: [{ queue: 'events', ready: 2, delayed: 1, dead: 0 }, { queue: 'nlp', ready: 40, delayed: 0, dead: 3 }],
  ...over,
} as unknown as OpsHealth)
const job = (key: string, label: string, over = {}) => ({
  key, label, definition: `${label} definition`, window_basis: `${label} window basis`,
  queued: 3, running: 2, retrying: 1, failed: 4, lease_expired: 0, oldest_wait_seconds: 125,
  completed_in_window: 50, failed_in_window: 5, ...over,
})
const pipelines = (over = {}) => ({
  generated_at: AT, window_hours: 24, window_start: '2026-09-20T10:00:00Z',
  jobs: [
    job('article', 'Article fetch and extraction'),
    job('search', 'Search indexing', { queued: 0, running: 0, retrying: 7, failed: 0, oldest_wait_seconds: null, lease_expired: 2 }),
    job('nlp', 'NLP processing'), job('clustering', 'Story clustering'),
  ],
  events: {
    key: 'events', label: 'Event association', definition: 'Event definition', algorithm_version: 'rule-1', dirty_clusters: 9,
    last_run_at: '2026-09-21T09:55:00Z', last_success_at: '2026-09-21T09:50:00Z', runs_in_window: 12, failed_runs_in_window: 1,
    failed_clusters_in_window: 3, last_error: { at: '2026-09-21T09:55:00Z', category: 'event_association', message: 'ValueError: bad cluster' },
  },
  monitors: {
    key: 'monitors', label: 'Monitors', definition: 'Monitor definition', total: 6, enabled: 5, due: 2, oldest_overdue_seconds: 7200, in_error: 1,
    by_error_category: [{ category: 'search_unavailable', count: 1 }],
  },
  ...over,
} as unknown as OpsPipelines)
const feed = (id: string, name: string, state: string, over = {}) => ({
  id, name, enabled: state !== 'disabled', poll_interval_minutes: 30, state, last_fetch_status: 'success', last_fetch_at: '2026-09-21T09:30:00Z',
  last_success_at: '2026-09-21T09:30:00Z', next_poll_at: '2026-09-21T10:30:00Z', overdue_seconds: null, failure_streak: 0, streak_capped: false,
  failures_by_category: {}, ...over,
})
const feeds = (over = {}) => ({
  generated_at: AT, window_hours: 24, window_start: '2026-09-20T10:00:00Z', truncated: false,
  items: [
    feed('f1', 'Healthy Wire', 'ok'),
    feed('f2', 'Broken Daily', 'failing', { last_fetch_status: 'failed', failure_streak: 3, failures_by_category: { timeout: 2, http_transient: 1 } }),
    feed('f3', 'Stuck Post', 'overdue', { overdue_seconds: 10800 }),
    feed('f4', 'Endless Failure', 'failing', { last_fetch_status: 'failed', failure_streak: 20, streak_capped: true }),
    feed('f5', 'Paused Times', 'disabled'),
  ],
  totals: { fetches: 40, entries: 200, invalid: 10, new: 30, duplicates: 160 },
  ...over,
} as unknown as OpsFeeds)
const storage = (over = {}) => ({
  generated_at: AT, database_bytes: 5 * 1024 ** 2, retained_html_objects: 12, article_files_measured: false, article_files_note: 'Files live on the worker volume.',
  tables: [{ name: 'articles', total_bytes: 2 * 1024 ** 2, approximate_rows: 1234 }, { name: 'feed_fetches', total_bytes: 1024, approximate_rows: 0 }],
  elasticsearch: { index: 'articles-current', documents: 999, store_bytes: 3 * 1024 ** 2 }, elasticsearch_error: null, ...over,
} as unknown as OpsStorage)
const failures = (over = {}) => ({
  generated_at: AT, area: 'feed', window_hours: 24, window_start: '2026-09-20T10:00:00Z',
  by_category: [{ category: 'timeout', count: 5 }, { category: 'security', count: 1 }],
  recent: [{ id: 'x1', ref_id: 'f2', status: 'failed', at: '2026-09-21T09:30:00Z', error_category: 'timeout', message: 'The read operation timed out' }],
  ...over,
} as unknown as OpsFailures)

function open(search = '') {
  resetNavigationHarness({ pathname: '/operations/', search })
  return renderWithQuery(() => <OperationsPage />)
}
const stat = (panel: HTMLElement, label: string) => within(panel).getByText(label).nextElementSibling?.textContent

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.opsHealth).mockResolvedValue(health())
  vi.mocked(api.opsPipelines).mockResolvedValue(pipelines())
  vi.mocked(api.opsFeeds).mockResolvedValue(feeds())
  vi.mocked(api.opsStorage).mockResolvedValue(storage())
  vi.mocked(api.opsFailures).mockResolvedValue(failures())
})
afterEach(cleanup)

it('shows every dependency with its state, and an outage does not hide the others', async () => {
  open()
  const panel = await screen.findByRole('region', { name: 'Dependencies' })
  const rows = await within(panel).findAllByRole('listitem')
  expect(rows.map(row => row.textContent)).toEqual([
    expect.stringMatching(/PostgreSQL.*OK.*3 ms/),
    expect.stringMatching(/Redis.*OK/),
    expect.stringMatching(/Elasticsearch.*Down.*ConnectError/),
    expect.stringMatching(/NLP processors.*OK.*entities disabled by configuration/),
    expect.stringMatching(/Scheduler.*OK.*Last cycle 4 s ago/),
  ])
  expect(within(panel).getByText(/Workers have no heartbeat/)).toBeTruthy()
  const queues = within(panel).getByRole('table', { name: 'Queues' })
  expect(within(queues).getByRole('row', { name: /nlp 40 0 3/ })).toBeTruthy()
  expect(screen.getAllByText(/As of/).length).toBeGreaterThan(0)
})

it('lists each job pipeline with its counts, oldest wait and the definition of its window', async () => {
  open()
  const table = await screen.findByRole('table', { name: 'Job pipelines' })
  const article = within(table).getByRole('row', { name: /Article fetch and extraction/ })
  expect(within(article).getAllByRole('cell').map(cell => cell.textContent?.trim()).slice(0, 8)).toEqual(['3', '2', '1', '4', '0', '2 min', '50', '5'])
  const search = within(table).getByRole('row', { name: /Search indexing/ })
  expect(within(search).getAllByRole('cell').map(cell => cell.textContent?.trim()).slice(0, 6)).toEqual(['0', '0', '7', '0', '2', '—'])
  expect(screen.getByText('Article fetch and extraction definition')).toBeTruthy()
  expect(screen.getByText(/Article fetch and extraction window basis/)).toBeTruthy()
  expect(within(article).getByRole('link', { name: 'Jobs' }).getAttribute('href')).toBe('/jobs/')
  expect(within(within(table).getByRole('row', { name: /Story clustering/ })).queryByRole('link', { name: 'Jobs' })).toBeNull()
})

it('shows the event and monitor pipelines with their own fields, and monitors as counts only', async () => {
  open()
  const events = await screen.findByRole('region', { name: 'Event association' })
  expect(stat(events, 'Dirty clusters')).toBe('9')
  expect(stat(events, 'Last run')).toBe('5 min ago')
  expect(stat(events, 'Last run without failure')).toBe('10 min ago')
  expect(stat(events, 'Runs in window')).toBe('12')
  expect(stat(events, 'Failed runs')).toBe('1')
  expect(stat(events, 'Failed cluster decisions')).toBe('3')
  expect(within(events).getByText(/ValueError: bad cluster/)).toBeTruthy()
  const monitors = screen.getByRole('region', { name: 'Monitors' })
  expect(stat(monitors, 'Due now')).toBe('2')
  expect(stat(monitors, 'Oldest overdue')).toBe('2 h')
  expect(stat(monitors, 'In error')).toBe('1')
  expect(within(monitors).getByText(/search_unavailable/)).toBeTruthy()
  expect(within(monitors).getByText(/across all users/)).toBeTruthy()
})

it('lists feeds by state with their failure streak, and links each to its source dossier', async () => {
  open()
  const panel = await screen.findByRole('region', { name: 'Feeds' })
  const items = await within(panel).findAllByRole('listitem')
  expect(items).toHaveLength(5)
  const broken = items.find(item => item.textContent?.includes('Broken Daily'))!
  expect(broken.textContent).toMatch(/Failing/)
  expect(broken.textContent).toMatch(/3 failed fetches in a row/)
  expect(broken.textContent).toMatch(/timeout 2/)
  expect(items.find(item => item.textContent?.includes('Endless Failure'))!.textContent).toMatch(/20\+ failed fetches in a row/)
  expect(items.find(item => item.textContent?.includes('Stuck Post'))!.textContent).toMatch(/Overdue.*3 h/)
  expect(within(items[0]).getByRole('link', { name: 'Healthy Wire' }).getAttribute('href')).toMatch(/^\/sources\/detail\/\?id=f1/)
  expect(within(panel).getByText(/40 finished fetches/)).toBeTruthy()
  expect(within(panel).getByText(/160 duplicates/)).toBeTruthy()
  expect(within(panel).getByText(/entries − invalid − new/)).toBeTruthy()
})

it('filters feeds by state from the URL and writes a change back to it', async () => {
  open('feeds=failing')
  const panel = await screen.findByRole('region', { name: 'Feeds' })
  expect((await within(panel).findAllByRole('listitem')).map(item => item.textContent)).toEqual([
    expect.stringContaining('Broken Daily'), expect.stringContaining('Endless Failure'),
  ])
  expect((within(panel).getByLabelText('Show feeds') as HTMLSelectElement).value).toBe('failing')
  fireEvent.change(within(panel).getByLabelText('Show feeds'), { target: { value: 'overdue' } })
  expect(navigationHarness.replace).toHaveBeenCalledWith('/operations/?feeds=overdue')
})

it('says when no feed matches, when there are none, and when the list was cut off', async () => {
  vi.mocked(api.opsFeeds).mockResolvedValue(feeds({ items: [feed('f1', 'Healthy Wire', 'ok')], truncated: true }))
  open('feeds=failing')
  expect(await screen.findByText('No feeds are failing.')).toBeTruthy()
  expect(screen.getByText(/first 500 feeds/)).toBeTruthy()
  cleanup()
  vi.mocked(api.opsFeeds).mockResolvedValue(feeds({ items: [] }))
  open()
  expect(await screen.findByText('No feeds yet.')).toBeTruthy()
})

it('shows storage, and says so when Elasticsearch could not be measured or files cannot be', async () => {
  open()
  const panel = await screen.findByRole('region', { name: 'Storage' })
  expect(await within(panel).findByText(/Database.*5\.0 MiB/)).toBeTruthy()
  expect(within(within(panel).getByRole('table', { name: 'Tables' }).querySelectorAll('tr')[1] as HTMLElement).getAllByRole('cell').map(cell => cell.textContent)).toEqual(['articles', '2.0 MiB', '≈ 1234'])
  expect(within(panel).getByText(/999 documents.*3\.0 MiB/)).toBeTruthy()
  expect(within(panel).getByText('Files live on the worker volume.')).toBeTruthy()
  cleanup()
  vi.mocked(api.opsStorage).mockResolvedValue(storage({ elasticsearch: null, elasticsearch_error: 'ConnectError' }))
  open()
  expect(await screen.findByText(/Elasticsearch could not be measured \(ConnectError\)/)).toBeTruthy()
})

it('keeps every other panel when one request fails', async () => {
  vi.mocked(api.opsPipelines).mockRejectedValue(new Error('boom'))
  open()
  expect(await screen.findByText('Could not load the pipelines.')).toBeTruthy()
  expect(await screen.findByRole('region', { name: 'Dependencies' })).toBeTruthy()
  expect(await screen.findByRole('region', { name: 'Feeds' })).toBeTruthy()
  expect(await screen.findByRole('region', { name: 'Storage' })).toBeTruthy()
})

it('shows loading text before anything arrives', () => {
  vi.mocked(api.opsHealth).mockReturnValue(new Promise(() => {}))
  open()
  expect(screen.getByText('Checking dependencies…')).toBeTruthy()
})

it('passes the window from the URL to the windowed requests and writes a new one back', async () => {
  open('hours=72')
  await screen.findByRole('table', { name: 'Job pipelines' })
  expect(api.opsPipelines).toHaveBeenCalledWith(72)
  expect(api.opsFeeds).toHaveBeenCalledWith(72)
  expect((screen.getByLabelText('Window') as HTMLSelectElement).value).toBe('72')
  fireEvent.change(screen.getByLabelText('Window'), { target: { value: '6' } })
  expect(navigationHarness.replace).toHaveBeenCalledWith('/operations/?hours=6')
})

it('ignores a window, area or feed filter the API would not accept', async () => {
  open('hours=9999&area=nonsense&feeds=nonsense')
  await screen.findByRole('table', { name: 'Job pipelines' })
  expect(api.opsPipelines).toHaveBeenCalledWith(24)
  expect(api.opsFailures).not.toHaveBeenCalled()
  expect(within(screen.getByRole('region', { name: 'Feeds' })).getAllByRole('listitem')).toHaveLength(5)
})

it('opens a failure drill-down from a pipeline row and keeps it in the URL', async () => {
  open()
  const table = await screen.findByRole('table', { name: 'Job pipelines' })
  fireEvent.click(within(within(table).getByRole('row', { name: /Search indexing/ })).getByRole('button', { name: 'Failures' }))
  expect(navigationHarness.replace).toHaveBeenCalledWith('/operations/?area=search')
})

it('shows failure categories and the newest failures for the area in the URL, and closes it', async () => {
  open('area=feed&hours=72')
  const panel = await screen.findByRole('region', { name: 'Failures: Feed fetches' })
  expect(api.opsFailures).toHaveBeenCalledWith('feed', 72)
  const categories = await within(panel).findByRole('list', { name: 'Failure categories' })
  expect(within(categories).getByText(/timeout/).textContent).toMatch(/5/)
  expect(within(categories).getAllByRole('listitem')).toHaveLength(2)
  expect(await within(panel).findByText('The read operation timed out')).toBeTruthy()
  fireEvent.click(within(panel).getByRole('button', { name: 'Close' }))
  expect(navigationHarness.replace).toHaveBeenCalledWith('/operations/?hours=72')
})

it('shows a monitor failure without a message, and an empty area as none', async () => {
  vi.mocked(api.opsFailures).mockResolvedValue(failures({ area: 'monitor', recent: [{ id: 'm1', ref_id: null, status: null, at: AT, error_category: 'search_unavailable', message: null }] }))
  open('area=monitor')
  const panel = await screen.findByRole('region', { name: 'Failures: Monitors' })
  expect((await within(panel).findAllByText(/search_unavailable/)).length).toBeGreaterThan(0)
  cleanup()
  vi.mocked(api.opsFailures).mockResolvedValue(failures({ by_category: [], recent: [] }))
  open('area=feed')
  expect(await screen.findByText('No failures in the last 24 hours.')).toBeTruthy()
})

it('reports a failed drill-down without hiding the page', async () => {
  vi.mocked(api.opsFailures).mockRejectedValue(new Error('boom'))
  open('area=feed')
  expect(await screen.findByText('Could not load the failures.')).toBeTruthy()
  await waitFor(() => expect(screen.getByRole('region', { name: 'Dependencies' })).toBeTruthy())
})
