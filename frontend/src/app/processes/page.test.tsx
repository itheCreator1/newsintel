import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '../../lib/api'
import type { ActivityPage, NlpStatus, OpsFeeds, OpsHealth, OpsStorage, OpsWikidata, ProcessesResponse } from '../../lib/api-types'
import { navigationHarness, resetNavigationHarness } from '../../test/navigation-harness'
import { renderWithQuery } from '../../test/render'
import ProcessesPage from './page'

vi.mock('../../lib/api', async importOriginal => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: {
    processes: vi.fn(), processActivity: vi.fn(), retryProcessItem: vi.fn(), retryProcessFailed: vi.fn(), runProcess: vi.fn(), stopProcessRun: vi.fn(),
    opsHealth: vi.fn(), opsFeeds: vi.fn(), opsStorage: vi.fn(), opsWikidata: vi.fn(), pollFeed: vi.fn(), nlpStatus: vi.fn(),
  },
}))

const AT = '2026-09-21T10:00:00Z'
const card = (key: string, group: string, label: string, over = {}) => ({
  key, group, label, description: `${label} description`, state: 'ok', queued: null, running: null, retrying: null, failed: null,
  lease_expired: null, oldest_wait_seconds: null, done_in_window: null, failed_in_window: null, last_run_at: null,
  active_run_id: null, progress: null, detail: null, actions: [], ...over,
})
const counts = (queued: number, running: number, retrying: number, failed: number) => ({ queued, running, retrying, failed, lease_expired: 0 })
const processes = (over = {}) => ({
  generated_at: AT, window_hours: 24, window_start: '2026-09-20T10:00:00Z',
  processes: [
    card('feeds', 'per_item', 'Feed fetching', { ...counts(0, 2, 0, 1), state: 'failing', failed_in_window: 1, done_in_window: 486, last_run_at: '2026-09-21T09:59:52Z', actions: ['retry_failed'] }),
    card('articles', 'per_item', 'Article download', { ...counts(37, 4, 2, 3), state: 'failing', failed_in_window: 3, done_in_window: 1204, oldest_wait_seconds: 41, actions: ['retry_failed'] }),
    card('nlp', 'per_item', 'NLP', { ...counts(112, 2, 0, 0), state: 'working', done_in_window: 1188, oldest_wait_seconds: 120 }),
    card('clustering', 'per_item', 'Story clustering', { ...counts(0, 0, 0, 0), done_in_window: 1171 }),
    card('search', 'per_item', 'Search indexing', { ...counts(3, 0, 9, 0), state: 'retrying' }),
    card('monitors', 'per_item', 'Watchlist monitors', { ...counts(0, 0, 0, 0) }),
    card('reprocessing', 'bulk', 'NLP reprocessing', { state: 'working', active_run_id: 'run-1', progress: { done: 29840, total: 48210 }, detail: 'entities', actions: ['stop'] }),
    card('authority', 'bulk', 'Name changes', { state: 'working', progress: { done: 212, total: 340 }, detail: 'Merge into “Alexis Tsipras”' }),
    card('rebuild', 'bulk', 'Search index rebuild', { state: 'idle' }),
    card('source_refresh', 'bulk', 'Source reindex', { state: 'idle' }),
    card('events', 'scheduled', 'Event linking', { last_run_at: '2026-09-21T09:54:00Z', actions: ['run_now'] }),
    card('retention', 'scheduled', 'History cleanup', { last_run_at: '2026-09-21T09:38:00Z', detail: 'Removed 1,930 rows', actions: ['run_now'] }),
    card('wikidata_refresh', 'scheduled', 'Wikidata refresh', { detail: '412 checked, 9 changed, 0 errors', actions: ['run_now'] }),
    card('wikidata_candidates', 'scheduled', 'Wikidata suggestions', { state: 'off', detail: 'Wikidata is switched off' }),
  ],
  ...over,
} as unknown as ProcessesResponse)
const row = (process: string, id: string, status: string, title: string, over = {}) => ({
  process, id, status, title, detail: null, link_kind: null, link_id: null, at: '2026-09-21T09:58:00Z',
  error_category: null, error_message: null, attempt_count: null, attempts: [], can_retry: false, can_stop: false, ...over,
})
const activity = (over = {}) => ({
  generated_at: AT, window_hours: 24, window_start: '2026-09-20T10:00:00Z', next_cursor: null,
  counts: { attention: 4, failed: 2, running: 2, queued: 37 },
  items: [
    row('articles', 'job-1', 'failed', 'Ο Μητσοτάκης στις Βρυξέλλες', {
      detail: 'Kathimerini · extract', link_kind: 'article', link_id: 'article-1', error_category: 'extract', error_message: 'No readable text found', can_retry: true,
      attempt_count: 3, attempts: [1, 2, 3].map(number => ({ number, stage: 'extract', status: 'failed', error_category: 'extract', error_message: `try ${number}`, started_at: AT })),
    }),
    row('feeds', 'fetch-1', 'failed', 'Kathimerini, Politics', { link_kind: 'feed', link_id: 'feed-1', error_category: 'http', error_message: 'HTTP 503', can_retry: true }),
    row('reprocessing', 'run-1', 'running', 'NLP reprocessing', { detail: '29840 articles scanned, 812 jobs queued', can_stop: true }),
    row('nlp', 'nlp-1', 'running', 'NATO ministers meet', { link_kind: 'article', link_id: 'article-2' }),
  ],
  ...over,
} as unknown as ActivityPage)
const probe = (name: string, state: string, over = {}) => ({ name, state, latency_ms: 3, detail: null, checked_at: AT, ...over })
const health = () => ({
  generated_at: AT,
  probes: [
    probe('postgres', 'ok'), probe('redis', 'ok'), probe('elasticsearch', 'down', { latency_ms: null, detail: 'ConnectError' }),
    probe('nlp', 'ok', { detail: 'entities disabled by configuration' }), probe('scheduler', 'ok', { detail: 'Last cycle 4 s ago' }),
    probe('workers', 'down', { detail: 'No live worker on nlp (312 s ago)' }),
  ],
  queues: [{ queue: 'events', ready: 2, delayed: 1, dead: 0 }, { queue: 'nlp', ready: 40, delayed: 0, dead: 3 }],
} as unknown as OpsHealth)
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
    feed('f4', 'Paused Times', 'disabled'),
  ],
  totals: { fetches: 40, entries: 200, invalid: 10, new: 30, duplicates: 160 },
  ...over,
} as unknown as OpsFeeds)
const storage = (over = {}) => ({
  generated_at: AT, database_bytes: 5 * 1024 ** 2, retained_html_objects: 12, article_files_measured: false, article_files_note: 'Files live on the worker volume.',
  tables: [{ name: 'articles', total_bytes: 2 * 1024 ** 2, approximate_rows: 1234 }],
  elasticsearch: { index: 'articles-current', documents: 999, store_bytes: 3 * 1024 ** 2 }, elasticsearch_error: null, ...over,
} as unknown as OpsStorage)
const wdRun = (kind: string, status: string, over = {}) => ({
  id: `${kind}-${status}`, kind, status, entity_id: null, checked: 40, changed: 3, redirected: 1, missing: 0, errors: 0, requests: 2,
  error: null, created_at: AT, started_at: AT, finished_at: '2026-09-21T10:05:00Z', ...over,
})
const wikidata = (over = {}) => ({
  enabled: true, reason: null,
  throttle: { state: 'open', paused_until: null, pause_reason: null, requests_today: 312, daily_budget: 2000, next_request_at: null },
  counts: [{ day: '2026-09-21', kind: 'search', outcome: 'ok', count: 300, average_ms: 210 }],
  links: 41, open_candidates: 7, due_refresh: 2,
  runs: [wdRun('refresh', 'finished'), wdRun('candidates', 'failed', { error: 'Wikidata answered 503', checked: 0, finished_at: null })],
  last_refresh: wdRun('refresh', 'finished'),
  ...over,
} as unknown as OpsWikidata)
const nlp = { queued: 0, running: 0, retrying: 0, failed: 0, capabilities: [{ name: 'keywords', state: 'ready', detail: null, version: '1' }, { name: 'entities', state: 'disabled', detail: 'off', version: null }], reprocessing: [] } as unknown as NlpStatus

function open(search = '') {
  resetNavigationHarness({ pathname: '/processes/', search })
  return renderWithQuery(() => <ProcessesPage />)
}
const region = (name: string) => screen.findByRole('region', { name })
const cardFor = async (label: string) => (await screen.findByRole('button', { name: label })).closest('article') as HTMLElement
const stat = (panel: HTMLElement, label: string) => within(panel).getByText(label).nextElementSibling?.textContent

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.processes).mockResolvedValue(processes())
  vi.mocked(api.processActivity).mockResolvedValue(activity())
  vi.mocked(api.opsHealth).mockResolvedValue(health())
  vi.mocked(api.opsFeeds).mockResolvedValue(feeds())
  vi.mocked(api.opsStorage).mockResolvedValue(storage())
  vi.mocked(api.opsWikidata).mockResolvedValue(wikidata())
  vi.mocked(api.nlpStatus).mockResolvedValue(nlp)
  vi.mocked(api.retryProcessItem).mockResolvedValue({ status: 'queued' })
  vi.mocked(api.retryProcessFailed).mockResolvedValue({ retried: 3, remaining: 0 })
  vi.mocked(api.runProcess).mockResolvedValue({ status: 'sent', run_id: null })
  vi.mocked(api.stopProcessRun).mockResolvedValue({ status: 'stopped' })
  vi.mocked(api.pollFeed).mockResolvedValue({ fetch_id: 'x', status: 'queued', reused: false })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('shows every service with its state, and an outage does not hide the others', async () => {
  open()
  const panel = await region('Services')
  const items = await within(panel).findAllByRole('listitem')
  expect(items.map(item => item.textContent)).toEqual([
    expect.stringMatching(/PostgreSQL.*OK.*3 ms/), expect.stringMatching(/Redis.*OK/),
    expect.stringMatching(/Elasticsearch.*Down.*ConnectError/), expect.stringMatching(/NLP models.*OK.*entities disabled/),
    expect.stringMatching(/Scheduler.*OK.*Last cycle 4 s ago/), expect.stringMatching(/Workers.*Down.*No live worker on nlp/),
  ])
  expect(within(within(panel).getByRole('table', { name: 'Queues' })).getByRole('row', { name: /nlp 40 0 3/ })).toBeTruthy()
})

it('shows one card per process, in three groups and in page order', async () => {
  open()
  const panel = await region('Processes')
  await within(panel).findByText('Feed fetching')
  const groups = ['Per item', 'Bulk runs', 'Scheduled'].map(name => within(panel).getByRole('group', { name }))
  expect(groups.map(group => within(group).getAllByRole('heading', { level: 4 }).map(heading => heading.textContent))).toEqual([
    ['Feed fetching', 'Article download', 'NLP', 'Story clustering', 'Search indexing', 'Watchlist monitors'],
    ['NLP reprocessing', 'Name changes', 'Search index rebuild', 'Source reindex'],
    ['Event linking', 'History cleanup', 'Wikidata refresh', 'Wikidata suggestions'],
  ])
})

it('shows a card’s counts, state, progress and newest run, and marks the failing ones', async () => {
  open()
  const articles = await cardFor('Article download')
  expect(articles.dataset.attention).toBe('true')
  expect(within(articles).getByText('3 failed')).toBeTruthy()
  expect(stat(articles, 'Queued')).toBe('37')
  expect(stat(articles, 'Failed')).toBe('3')
  expect(articles.textContent).toMatch(/Oldest wait 41 s/)
  expect(articles.textContent).toMatch(/Done in 24 h 1,204/)
  expect((await cardFor('NLP')).dataset.attention).toBe('false')
  const reprocessing = await cardFor('NLP reprocessing')
  expect(within(reprocessing).getByText('62%')).toBeTruthy()
  expect(within(reprocessing).getByRole('progressbar').getAttribute('aria-valuenow')).toBe('62')
  expect(reprocessing.textContent).toMatch(/29,840 of 48,210/)
  expect((await cardFor('Name changes')).textContent).toMatch(/Merge into “Alexis Tsipras”/)
  expect((await cardFor('History cleanup')).textContent).toMatch(/Last run 22 min ago.*Removed 1,930 rows/)
  expect(within(await cardFor('Wikidata suggestions')).getByText('Off')).toBeTruthy()
})

it('names the NLP models on the NLP card', async () => {
  open()
  expect(await within(await cardFor('NLP')).findByText('Models: keywords ready · entities disabled')).toBeTruthy()
})

it('filters the activity list to a process when its card is clicked, and clears it on a second click', async () => {
  const view = open()
  fireEvent.click(await screen.findByRole('button', { name: 'Search indexing' }))
  expect(navigationHarness.replace).toHaveBeenLastCalledWith('/processes/?process=search&status=all')
  view.rerenderSame()
  expect(screen.getByRole('button', { name: 'Search indexing' }).getAttribute('aria-pressed')).toBe('true')
  fireEvent.click(screen.getByRole('button', { name: 'Search indexing' }))
  expect(navigationHarness.replace).toHaveBeenLastCalledWith('/processes/?status=all')
})

it('retries everything a process failed and says how many it retried', async () => {
  open()
  const articles = await cardFor('Article download')
  fireEvent.click(within(articles).getByRole('button', { name: 'Retry 3 failed' }))
  await waitFor(() => expect(api.retryProcessFailed).toHaveBeenCalledWith('articles'))
  expect(await within(articles).findByText('Retried 3.')).toBeTruthy()
  vi.mocked(api.retryProcessFailed).mockResolvedValue({ retried: 200, remaining: 54 })
  fireEvent.click(within(articles).getByRole('button', { name: 'Retry 3 failed' }))
  expect(await within(articles).findByText('Retried 200; 54 still failed.')).toBeTruthy()
  expect(api.processes).toHaveBeenCalledTimes(3)
})

it('runs a scheduled process now, and stops a bulk run after asking', async () => {
  open()
  const events = await cardFor('Event linking')
  fireEvent.click(within(events).getByRole('button', { name: 'Run now' }))
  await waitFor(() => expect(api.runProcess).toHaveBeenCalledWith('events'))
  expect(await within(events).findByText('Started.')).toBeTruthy()
  vi.mocked(api.runProcess).mockResolvedValue({ status: 'queued', run_id: 'wd-1' })
  const refresh = await cardFor('Wikidata refresh')
  fireEvent.click(within(refresh).getByRole('button', { name: 'Run now' }))
  expect(await within(refresh).findByText('Queued; it starts within a few seconds.')).toBeTruthy()

  const confirm = vi.fn(() => false)
  vi.stubGlobal('confirm', confirm)
  const reprocessing = await cardFor('NLP reprocessing')
  fireEvent.click(within(reprocessing).getByRole('button', { name: 'Stop' }))
  expect(confirm).toHaveBeenCalled()
  expect(api.stopProcessRun).not.toHaveBeenCalled()
  confirm.mockReturnValue(true)
  fireEvent.click(within(reprocessing).getByRole('button', { name: 'Stop' }))
  await waitFor(() => expect(api.stopProcessRun).toHaveBeenCalledWith('reprocessing', 'run-1'))
  expect(await within(reprocessing).findByText('Stopped.')).toBeTruthy()
  expect(within(await cardFor('Wikidata suggestions')).queryByRole('button', { name: 'Run now' })).toBeNull()
})

it('says why an action was refused', async () => {
  vi.mocked(api.runProcess).mockRejectedValue(Object.assign(new Error('Wikidata is switched off: no contact'), { status: 409 }))
  open()
  const refresh = await cardFor('Wikidata refresh')
  fireEvent.click(within(refresh).getByRole('button', { name: 'Run now' }))
  expect(await within(refresh).findByText('Wikidata is switched off: no contact')).toBeTruthy()
})

it('asks for what needs attention by default, and passes the filters from the URL', async () => {
  open()
  await screen.findByText('Ο Μητσοτάκης στις Βρυξέλλες')
  expect(api.processActivity).toHaveBeenCalledWith({ hours: 24, process: undefined, status: 'attention', q: undefined, cursor: undefined })
  cleanup()
  open('hours=168&process=feeds&status=failed&q=wire')
  await screen.findByText('Kathimerini, Politics')
  expect(api.processActivity).toHaveBeenLastCalledWith({ hours: 168, process: 'feeds', status: 'failed', q: 'wire', cursor: undefined })
  expect((screen.getByLabelText('Process') as HTMLSelectElement).value).toBe('feeds')
  expect((screen.getByLabelText('Find') as HTMLInputElement).value).toBe('wire')
})

it('shows the status chips with their counts and writes a choice to the URL', async () => {
  open()
  const chips = await screen.findByRole('group', { name: 'Status' })
  await within(chips).findByRole('button', { name: 'Needs attention 4' })
  expect(within(chips).getAllByRole('button').map(chip => chip.textContent)).toEqual(['Needs attention4', 'Failed2', 'Running2', 'Queued37', 'Done', 'All'])
  expect(within(chips).getByRole('button', { name: 'Needs attention 4' }).getAttribute('aria-pressed')).toBe('true')
  fireEvent.click(within(chips).getByRole('button', { name: 'Done' }))
  expect(navigationHarness.replace).toHaveBeenLastCalledWith('/processes/?status=done')
  fireEvent.change(screen.getByLabelText('Process'), { target: { value: 'nlp' } })
  expect(navigationHarness.replace).toHaveBeenLastCalledWith('/processes/?process=nlp')
})

it('searches the activity list when the search is submitted', async () => {
  open()
  const find = await screen.findByLabelText('Find')
  fireEvent.change(find, { target: { value: ' Kathimerini ' } })
  fireEvent.submit(find.closest('form')!)
  expect(navigationHarness.replace).toHaveBeenLastCalledWith('/processes/?q=Kathimerini')
})

it('shows each row with its process, a link, the error, the attempts and its actions', async () => {
  open()
  const list = await screen.findByRole('list', { name: 'Activity' })
  await within(list).findAllByRole('listitem')
  const rows = Array.from(list.querySelectorAll(':scope > li')) as HTMLElement[]
  const failed = rows[0]
  expect(failed.textContent).toMatch(/Article download/)
  expect(within(failed).getByRole('link', { name: 'Ο Μητσοτάκης στις Βρυξέλλες' }).getAttribute('href')).toBe('/articles/?article=article-1')
  expect(failed.textContent).toMatch(/Kathimerini · extract/)
  expect(within(failed).getByText('extract', { selector: 'code' })).toBeTruthy()
  expect(failed.textContent).toMatch(/No readable text found/)
  expect(within(failed).getByText('3 attempts')).toBeTruthy()
  expect(within(failed).getByText('Failed')).toBeTruthy()
  expect(within(rows[1]).getByRole('link', { name: 'Kathimerini, Politics' }).getAttribute('href')).toBe('/sources/detail/?id=feed-1')
  expect(within(rows[2]).queryByRole('link')).toBeNull()
  expect(within(rows[3]).queryByRole('button')).toBeNull()

  fireEvent.click(within(failed).getByRole('button', { name: 'Retry' }))
  await waitFor(() => expect(api.retryProcessItem).toHaveBeenCalledWith('articles', 'job-1'))
  expect(await screen.findByText('Retry scheduled.')).toBeTruthy()

  vi.stubGlobal('confirm', vi.fn(() => true))
  fireEvent.click(within(rows[2]).getByRole('button', { name: 'Stop' }))
  await waitFor(() => expect(api.stopProcessRun).toHaveBeenCalledWith('reprocessing', 'run-1'))
})

it('loads the next page of activity under the first', async () => {
  vi.mocked(api.processActivity)
    .mockResolvedValueOnce(activity({ next_cursor: 'next-1' }))
    .mockResolvedValueOnce(activity({ items: [row('clustering', 'c-9', 'succeeded', 'Older story')], next_cursor: null }))
  open()
  fireEvent.click(await screen.findByRole('button', { name: 'Load more' }))
  expect(await screen.findByText('Older story')).toBeTruthy()
  expect(api.processActivity).toHaveBeenLastCalledWith({ hours: 24, process: undefined, status: 'attention', q: undefined, cursor: 'next-1' })
  expect(screen.getByText('Ο Μητσοτάκης στις Βρυξέλλες')).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Load more' })).toBeNull()
})

it('says when nothing needs attention, and when nothing matches', async () => {
  vi.mocked(api.processActivity).mockResolvedValue(activity({ items: [], counts: { attention: 0, failed: 0, running: 0, queued: 0 } }))
  open()
  expect(await screen.findByText('Nothing needs attention.')).toBeTruthy()
  cleanup()
  open('status=failed&process=nlp')
  expect(await screen.findByText('Nothing matches for NLP.')).toBeTruthy()
})

it('passes the window to the windowed requests and writes a new one to the URL', async () => {
  open('hours=1')
  await screen.findByRole('button', { name: 'Feed fetching' })
  expect(api.processes).toHaveBeenCalledWith(1)
  expect(api.opsFeeds).toHaveBeenCalledWith(1)
  const window = screen.getByRole('group', { name: 'Time window' })
  expect(within(window).getByRole('button', { name: '1 h' }).getAttribute('aria-pressed')).toBe('true')
  fireEvent.click(within(window).getByRole('button', { name: '7 d' }))
  expect(navigationHarness.replace).toHaveBeenLastCalledWith('/processes/?hours=168')
})

it('refreshes live by default and can be paused', async () => {
  open()
  const live = await screen.findByRole('button', { name: 'Live, every 5 s' })
  expect(live.getAttribute('aria-pressed')).toBe('true')
  fireEvent.click(live)
  expect(screen.getByRole('button', { name: 'Paused' }).getAttribute('aria-pressed')).toBe('false')
})

it('lists feeds with their state and failure streak, fetches one now, and filters to those needing attention', async () => {
  const view = open()
  const panel = await region('Feeds')
  const items = await within(panel).findAllByRole('listitem')
  expect(items).toHaveLength(4)
  const broken = items.find(item => item.textContent?.includes('Broken Daily'))!
  expect(broken.textContent).toMatch(/Failing.*3 failed fetches in a row/)
  expect(broken.textContent).toMatch(/timeout 2/)
  expect(within(items[0]).getByRole('link', { name: 'Healthy Wire' }).getAttribute('href')).toMatch(/^\/sources\/detail\/\?id=f1/)
  expect(within(panel).getByText(/40 finished fetches.*160 duplicates/)).toBeTruthy()
  fireEvent.click(within(broken).getByRole('button', { name: 'Fetch now' }))
  await waitFor(() => expect(api.pollFeed).toHaveBeenCalledWith('f2'))
  expect(await within(broken).findByText('Fetch queued.')).toBeTruthy()
  expect(within(items.find(item => item.textContent?.includes('Paused Times'))!).queryByRole('button', { name: 'Fetch now' })).toBeNull()

  fireEvent.click(within(panel).getByRole('button', { name: 'Needs attention 2' }))
  expect(navigationHarness.replace).toHaveBeenLastCalledWith('/processes/?feeds=attention')
  view.rerenderSame()
  expect(within(await region('Feeds')).getAllByRole('listitem').map(item => item.textContent)).toEqual([
    expect.stringContaining('Broken Daily'), expect.stringContaining('Stuck Post'),
  ])
})

it('shows storage, and says so when Elasticsearch could not be measured', async () => {
  open()
  const panel = await region('Storage')
  expect(await within(panel).findByText(/Database.*5\.0 MiB/)).toBeTruthy()
  expect(within(panel).getByText(/999 documents.*3\.0 MiB/)).toBeTruthy()
  expect(within(within(panel).getByRole('table', { name: 'Tables' })).getByRole('row', { name: /articles 2\.0 MiB ≈ 1234/ })).toBeTruthy()
  cleanup()
  vi.mocked(api.opsStorage).mockResolvedValue(storage({ elasticsearch: null, elasticsearch_error: 'ConnectError' }))
  open()
  expect(await screen.findByText(/Elasticsearch could not be measured \(ConnectError\)/)).toBeTruthy()
})

it('keeps the Wikidata panel: requests, links and recent runs', async () => {
  open()
  const panel = await region('Wikidata')
  await within(panel).findByText('Open')
  expect(stat(panel, 'Requests today')).toBe('312 of 2000')
  expect(stat(panel, 'Linked entities')).toBe('41')
  expect(within(within(panel).getByRole('table', { name: 'Wikidata runs' })).getByRole('row', { name: /candidates failed/ }).textContent).toContain('Wikidata answered 503')
  cleanup()
  vi.mocked(api.opsWikidata).mockResolvedValue(wikidata({ enabled: false, reason: 'Wikidata is switched off' }))
  open()
  expect(await within(await region('Wikidata')).findByText('Off: Wikidata is switched off')).toBeTruthy()
})

it('keeps every other panel when one request fails', async () => {
  vi.mocked(api.processes).mockRejectedValue(new Error('boom'))
  vi.mocked(api.processActivity).mockRejectedValue(new Error('boom'))
  open()
  expect(await screen.findByText('Could not load the processes.')).toBeTruthy()
  expect(await screen.findByText('Could not load the activity.')).toBeTruthy()
  expect(await region('Services')).toBeTruthy()
  expect(await region('Feeds')).toBeTruthy()
  expect(await region('Storage')).toBeTruthy()
})

it('ignores a window, process, status or feed filter the API would not accept', async () => {
  open('hours=9999&process=nonsense&status=nonsense&feeds=nonsense')
  await screen.findByRole('button', { name: 'Feed fetching' })
  expect(api.processes).toHaveBeenCalledWith(24)
  expect(api.processActivity).toHaveBeenCalledWith({ hours: 24, process: undefined, status: 'attention', q: undefined, cursor: undefined })
})
