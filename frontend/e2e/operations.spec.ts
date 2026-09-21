import { expect, test, type Locator, type Page } from '@playwright/test'

async function login(page: Page) {
  await page.goto('/')
  await page.getByLabel('Username').fill('phase13d')
  await page.getByLabel('Password').fill('phase13d-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('link', { name: 'Saved Searches' })).toBeVisible()
}

const probe = (page: Page, name: string) => page.getByRole('region', { name: 'Dependencies' }).getByRole('listitem').filter({ hasText: name })
const counts = async (page: Page, path: string) => page.evaluate(async p => {
  const body = await (await fetch(`/api/v1${p}`)).json()
  return [body.queued, body.running, body.retrying, body.failed] as number[]
}, path)
const shown = async (row: Locator) => (await row.getByRole('cell').allTextContents()).slice(0, 4).map(Number)

// Runs after `relationships seed`, so feeds, jobs, indexing, NLP and monitors all have real state.
test('operations workflow shows healthy dependencies, matches the job pages and drills into failures', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await login(page)
  await page.getByRole('link', { name: 'Operations', exact: true }).click()
  await expect(page).toHaveURL(/\/operations\/$/)

  // Every dependency answers, including the scheduler heartbeat and the Dramatiq queues in Redis.
  for (const name of ['PostgreSQL', 'Redis', 'Elasticsearch', 'Scheduler', 'Workers']) await expect(probe(page, name)).toContainText('OK', { timeout: 30_000 })
  await expect(probe(page, 'Scheduler')).toContainText(/Last cycle \d+ s ago/)
  const queues = page.getByRole('table', { name: 'Queues' })
  for (const queue of ['default', 'search', 'monitors', 'nlp', 'clustering', 'events']) await expect(queues.getByRole('row', { name: new RegExp(`^${queue} `) })).toBeVisible()

  // The pipeline counts are the same numbers the existing job pages read, taken at the same moment.
  const table = page.getByRole('table', { name: 'Job pipelines' })
  const same: [string, string][] = [
    ['Article fetch and extraction', '/jobs/backlog'], ['Search indexing', '/search/indexing/status'],
    ['NLP processing', '/nlp/status'], ['Story clustering', '/clustering/status'],
  ]
  for (const [label, path] of same) {
    const row = table.getByRole('row', { name: new RegExp(label) })
    await expect.poll(async () => (await shown(row)).join() === (await counts(page, path)).join(), { timeout: 40_000, message: `${label} matches ${path}` }).toBe(true)
  }
  await expect(page.getByRole('region', { name: 'Event association' }).getByText('Dirty clusters')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Monitors' }).getByText(/across all users/)).toBeVisible()

  // Both seeded feeds are listed with a state, and the window's fetch totals carry their base.
  const feeds = page.getByRole('region', { name: 'Feeds' })
  for (const name of ['Relationships Wire', 'Relationships Daily']) await expect(feeds.getByRole('listitem').filter({ hasText: name })).toContainText('OK')
  await expect(feeds.getByText(/finished fetches? returned .* duplicates?/)).toBeVisible()

  // Storage from the catalogue and the search index.
  const storage = page.getByRole('region', { name: 'Storage' })
  await expect(storage.getByRole('table', { name: 'Tables' }).getByRole('row', { name: /^articles / })).toBeVisible()
  await expect(storage.getByText(/Elasticsearch articles-current: \d+ documents?/)).toBeVisible()

  // A failure drill-down is a bookmarkable part of the URL, and so is the window.
  await feeds.getByRole('button', { name: 'Failures' }).click()
  await expect(page).toHaveURL(/area=feed/)
  const failures = page.getByRole('region', { name: 'Failures: Feed fetches' })
  await expect(failures.getByText(/No failures in the last 24 hours\.|:/).first()).toBeVisible()
  await page.getByLabel('Window').selectOption('168')
  await expect(page).toHaveURL(/hours=168/)
  await page.goto(page.url())
  await expect(page.getByLabel('Window')).toHaveValue('168')
  await expect(page.getByRole('region', { name: 'Failures: Feed fetches' })).toBeVisible()
  await page.getByRole('region', { name: 'Failures: Feed fetches' }).getByRole('button', { name: 'Close' }).click()
  await expect(page).not.toHaveURL(/area=/)

  // The Jobs page keeps its retry lists and points here.
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Jobs', exact: true }).click()
  await page.getByRole('link', { name: 'Operational metrics' }).click()
  await expect(page).toHaveURL(/\/operations\/$/)
  expect(errors).toEqual([])
})

// The script stops Elasticsearch before this one: an outage must show as data, not as a broken page.
test('operations page reports a stopped Elasticsearch and keeps every other panel', async ({ page }) => {
  await login(page)
  await page.goto('/operations/')
  await expect(probe(page, 'Elasticsearch')).toContainText('Down', { timeout: 30_000 })
  await expect(probe(page, 'PostgreSQL')).toContainText('OK')
  await expect(probe(page, 'Redis')).toContainText('OK')
  await expect(page.getByRole('table', { name: 'Job pipelines' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Feeds' }).getByRole('listitem').first()).toBeVisible()
  await expect(page.getByRole('region', { name: 'Storage' }).getByText(/Elasticsearch could not be measured/)).toBeVisible({ timeout: 30_000 })
  await expect(page.getByRole('region', { name: 'Storage' }).getByRole('table', { name: 'Tables' })).toBeVisible()
})
