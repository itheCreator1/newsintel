import { expect, test, type Page } from '@playwright/test'

async function login(page: Page) {
  await page.goto('/')
  await page.getByLabel('Username').fill('phase13d')
  await page.getByLabel('Password').fill('phase13d-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('link', { name: 'Saved Searches' })).toBeVisible()
}

const service = (page: Page, name: string) => page.getByRole('region', { name: 'Services' }).getByRole('listitem').filter({ hasText: name })
const card = (page: Page, label: string) => page.getByRole('article').filter({ has: page.getByRole('button', { name: label, exact: true }) })
const counts = async (page: Page, path: string) => page.evaluate(async p => {
  const body = await (await fetch(`/api/v1${p}`)).json()
  return [body.queued, body.running, body.retrying, body.failed] as number[]
}, path)
const shown = async (page: Page, label: string) => Promise.all(['Queued', 'Running', 'Retrying', 'Failed'].map(async name =>
  Number(await card(page, label).getByText(name, { exact: true }).locator('xpath=following-sibling::dd').textContent())))
const finished = async (page: Page, process: string) => page.evaluate(async p => {
  const body = await (await fetch(`/api/v1/processes/activity?process=${p}&status=done&limit=100`)).json()
  return body.items.length as number
}, process)

// Runs after `relationships seed`, so feeds, jobs, indexing, NLP and monitors all have real state.
test('processes workflow shows healthy services, matches the job pages, filters activity and runs a process now', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await login(page)
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Processes', exact: true }).click()
  await expect(page).toHaveURL(/\/processes\/$/)

  // Every service answers, including the scheduler heartbeat and the Dramatiq queues in Redis.
  for (const name of ['PostgreSQL', 'Redis', 'Elasticsearch', 'Scheduler', 'Workers']) await expect(service(page, name)).toContainText('OK', { timeout: 30_000 })
  await expect(service(page, 'Scheduler')).toContainText(/Last cycle \d+ s ago/)
  const queues = page.getByRole('table', { name: 'Queues' })
  for (const queue of ['default', 'search', 'monitors', 'nlp', 'clustering', 'events']) await expect(queues.getByRole('row', { name: new RegExp(`^${queue} `) })).toHaveCount(1)

  // Fourteen cards in three groups; the per-article counts are the numbers the status endpoints give.
  const processes = page.getByRole('region', { name: 'Processes' })
  await expect(processes.getByRole('heading', { level: 4 })).toHaveCount(14)
  for (const [group, size] of [['Per item', 6], ['Bulk runs', 4], ['Scheduled', 4]] as const) await expect(processes.getByRole('group', { name: group }).getByRole('heading', { level: 4 })).toHaveCount(size)
  const same: [string, string][] = [
    ['Article download', '/jobs/backlog'], ['Search indexing', '/search/indexing/status'],
    ['NLP', '/nlp/status'], ['Story clustering', '/clustering/status'],
  ]
  for (const [label, path] of same) {
    await expect.poll(async () => (await shown(page, label)).join() === (await counts(page, path)).join(), { timeout: 40_000, message: `${label} matches ${path}` }).toBe(true)
  }

  // A card filters the activity list to its process; the filter is in the URL.
  await page.getByRole('button', { name: 'Story clustering', exact: true }).click()
  await expect(page).toHaveURL(/process=clustering/)
  await expect(page).toHaveURL(/status=all/)
  const activity = page.getByRole('list', { name: 'Activity' })
  await expect(activity.getByRole('listitem').first()).toContainText('Story clustering', { timeout: 30_000 })
  for (const text of await activity.getByRole('listitem').allTextContents()) expect(text).toContain('Story clustering')
  await page.getByRole('group', { name: 'Status' }).getByRole('button', { name: /^Done/ }).click()
  await expect(page).toHaveURL(/status=done/)

  // Run now sends the history cleanup to a worker, which records the run.
  const before = await finished(page, 'retention')
  await card(page, 'History cleanup').getByRole('button', { name: 'Run now' }).click()
  await expect(card(page, 'History cleanup').getByText('Started.')).toBeVisible()
  await expect.poll(() => finished(page, 'retention'), { timeout: 30_000 }).toBeGreaterThan(before)

  // Both seeded feeds are listed with a state, and the window's fetch totals carry their base.
  const feeds = page.getByRole('region', { name: 'Feeds' })
  for (const name of ['Relationships Wire', 'Relationships Daily']) await expect(feeds.getByRole('listitem').filter({ hasText: name })).toContainText('OK')
  await expect(feeds.getByText(/finished fetches? returned .* duplicates?/)).toBeVisible()

  // Storage from the catalogue and the search index.
  const storage = page.getByRole('region', { name: 'Storage' })
  await expect(storage.getByRole('table', { name: 'Tables' }).getByRole('row', { name: /^articles / })).toBeVisible()
  await expect(storage.getByText(/Elasticsearch articles-current: \d+ documents?/)).toBeVisible()

  // The window is part of the URL and survives a reload.
  await page.getByRole('group', { name: 'Time window' }).getByRole('button', { name: '7 d' }).click()
  await expect(page).toHaveURL(/hours=168/)
  await page.goto(page.url())
  await expect(page.getByRole('group', { name: 'Time window' }).getByRole('button', { name: '7 d' })).toHaveAttribute('aria-pressed', 'true')

  // The old Jobs and Operations addresses lead here.
  await page.goto('/operations/')
  await expect(page).toHaveURL(/\/processes\/$/)
  await page.goto('/jobs/?hours=168')
  await expect(page).toHaveURL(/\/processes\/\?hours=168$/)
  expect(errors).toEqual([])
})

// The script stops Elasticsearch before this one: an outage must show as data, not as a broken page.
test('processes page reports a stopped Elasticsearch and keeps every other panel', async ({ page }) => {
  await login(page)
  await page.goto('/processes/')
  await expect(service(page, 'Elasticsearch')).toContainText('Down', { timeout: 30_000 })
  await expect(service(page, 'PostgreSQL')).toContainText('OK')
  await expect(service(page, 'Redis')).toContainText('OK')
  await expect(page.getByRole('region', { name: 'Processes' }).getByRole('heading', { level: 4 })).toHaveCount(14)
  await expect(page.getByRole('region', { name: 'Feeds' }).getByRole('listitem').first()).toBeVisible()
  await expect(page.getByRole('region', { name: 'Storage' }).getByText(/Elasticsearch could not be measured/)).toBeVisible({ timeout: 30_000 })
  await expect(page.getByRole('region', { name: 'Storage' }).getByRole('table', { name: 'Tables' })).toBeVisible()

  // The recent map is SQL-backed and must keep working with Elasticsearch down.
  await page.goto('/map/?role=source&days=365')
  await expect(page.getByText(/of \d+ articles in the last 365 days have a source country/)).toBeVisible()
  await expect(page.getByRole('row', { name: /^Greece/ }).getByRole('cell').nth(1)).toHaveText('3')
  await expect(page.getByRole('region', { name: 'Investigation' })).toHaveCount(0)
  await expect(page.getByText('Could not load the map.')).toHaveCount(0)
})
