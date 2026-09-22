import { expect, test, type Page } from '@playwright/test'
import { fixtureDate } from './fixture-dates'

async function login(page: Page) {
  await page.goto('/')
  await page.getByLabel('Username').fill('phase6')
  await page.getByLabel('Password').fill('phase6-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('link', { name: 'Saved Searches' })).toBeVisible()
}

async function apiJson<T>(page: Page, path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  return page.evaluate(async ({ path, init }) => {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    if (init.method && init.method !== 'GET') headers['X-CSRF-Token'] = (await (await fetch('/api/v1/auth/csrf')).json()).csrf_token
    const response = await fetch(`/api/v1${path}`, { method: init.method ?? 'GET', headers, body: init.body === undefined ? undefined : JSON.stringify(init.body) })
    if (!response.ok) throw new Error(`${path} returned ${response.status}: ${await response.text()}`)
    return response.json()
  }, { path, init })
}

const heading = (page: Page) => page.getByRole('heading', { level: 3, name: /matching articles over time|Timeline/ })

test('investigation seed collects two dated sources', async ({ page }) => {
  await login(page)
  await apiJson(page, '/feeds', { method: 'POST', body: { name: 'Harbor Wire', url: 'http://fixture/investigations-wire.xml', source_country: 'GR' } })
  await apiJson(page, '/feeds', { method: 'POST', body: { name: 'Harbor Daily', url: 'http://fixture/investigations-daily.xml', source_country: 'US' } })
  await expect.poll(async () => {
    const page_ = await apiJson<{ items: { title: string }[] }>(page, '/articles?limit=100')
    return page_.items.filter(item => item.title.startsWith('Harbor')).length
  }, { timeout: 60_000, intervals: [1_000] }).toBe(6)
})

test('investigation workflow brushes, cross-filters, and saves searches', async ({ page }) => {
  await login(page)
  await page.goto('/search?q=Harbor')
  await expect(heading(page)).toHaveText('6 matching articles over time', { timeout: 30_000 })
  await expect(page.getByLabel('Timeline interval')).toHaveValue('auto')
  await expect(page.getByRole('option', { name: 'Automatic (day)' })).toBeAttached()

  // Twelve daily buckets span the fixtures' 1–12 September (shifted by the fixture server); brushing buckets 0 through 4 keeps 1–5 September.
  // The pixel math mirrors TimelineChart's grid (left 44, right 16) and the fixture feed dates; update both together.
  const chart = page.locator('.timeline-chart canvas').first()
  await expect(chart).toBeVisible()
  await chart.scrollIntoViewIfNeeded()
  const box = (await chart.boundingBox())!
  const plotLeft = box.x + 44, bucketWidth = (box.width - 60) / 12, y = box.y + box.height / 2
  await page.mouse.move(plotLeft + bucketWidth * 0.2, y)
  await page.mouse.down()
  await page.mouse.move(plotLeft + bucketWidth * 4.5, y, { steps: 12 })
  await page.mouse.up()
  await expect(page).toHaveURL(new RegExp(`after=${fixtureDate('2026-09-01')}`))
  await expect(page).toHaveURL(new RegExp(`before=${fixtureDate('2026-09-06')}`))
  await expect(heading(page)).toHaveText('4 matching articles over time')
  await expect(page.locator('.search-result')).toHaveCount(4)

  await page.getByRole('button', { name: 'Clear date range' }).click()
  await expect(page).not.toHaveURL(/after=/)
  await expect(heading(page)).toHaveText('6 matching articles over time')

  // Facets count the whole investigation; a value toggles the same bookmarkable filter the pickers use.
  const sourceFacets = page.getByRole('region', { name: 'Sources', exact: true })
  const wireFacet = sourceFacets.getByRole('button', { name: 'Harbor Wire 3', exact: true })
  await expect(wireFacet).toHaveAttribute('aria-pressed', 'false')
  await expect(sourceFacets.getByRole('button', { name: 'Harbor Daily 3', exact: true })).toBeVisible()
  await wireFacet.click()
  await expect(page).toHaveURL(/source_id=/)
  await expect(heading(page)).toHaveText('3 matching articles over time')
  await expect(wireFacet).toHaveAttribute('aria-pressed', 'true')
  await page.goBack()
  await expect(heading(page)).toHaveText('6 matching articles over time')
  await page.goForward()
  await expect(heading(page)).toHaveText('3 matching articles over time')

  // Graph receives the criteria it applies; a source filter is one of them.
  await page.getByRole('link', { name: 'Open in Graph' }).click()
  await expect(page).toHaveURL(/\/graph\/\?/)
  const graphUrl = new URL(page.url())
  expect(graphUrl.searchParams.get('q')).toBe('Harbor')
  expect(graphUrl.searchParams.getAll('source_id')).toHaveLength(1)
  await expect(page.getByLabel('Query', { exact: true })).toHaveValue('Harbor')
  await page.goBack()
  await expect(heading(page)).toHaveText('3 matching articles over time', { timeout: 30_000 })
  await wireFacet.click()
  await expect(page).not.toHaveURL(/source_id=/)
  await expect(heading(page)).toHaveText('6 matching articles over time')

  await page.getByRole('button', { name: 'Filter by source Harbor Wire' }).first().click()
  await expect(page).toHaveURL(/source_id=/)
  await expect(heading(page)).toHaveText('3 matching articles over time')
  await page.goBack()
  await expect(page).not.toHaveURL(/source_id=/)
  await expect(heading(page)).toHaveText('6 matching articles over time')
  await page.goForward()
  await expect(heading(page)).toHaveText('3 matching articles over time')

  await page.getByLabel('Timeline interval').selectOption('week')
  await expect(page).toHaveURL(/interval=week/)
  await page.getByLabel('Sort').selectOption('oldest')
  await page.getByRole('button', { name: 'Search archive' }).click()
  await expect(page).toHaveURL(/sort=oldest/)
  await page.reload()
  await expect(heading(page)).toHaveText('3 matching articles over time', { timeout: 30_000 })
  await expect(page.getByLabel('Timeline interval')).toHaveValue('week')
  const investigation = new URL(page.url())

  await page.getByLabel('Saved search name').fill('Wire watch')
  await page.getByRole('button', { name: 'Save search' }).click()
  await expect(page.getByText('Saved “Wire watch”.')).toBeVisible()
  await page.getByLabel('Saved search name').fill('wire WATCH')
  await page.getByRole('button', { name: 'Save search' }).click()
  // Next.js's App Router always renders a second, empty role="alert" node (its built-in route
  // announcer for screen readers) alongside the app's own error banner; scope to the latter.
  await expect(page.locator('p.error[role="alert"]')).toHaveText('A saved search with this name already exists')

  await page.goto('/search?q=Harbor%20AND%20daily')
  await expect(heading(page)).toHaveText('3 matching articles over time', { timeout: 30_000 })
  await page.getByLabel('Saved search name').fill('Stale investigation')
  await page.getByRole('button', { name: 'Save search' }).click()
  await expect(page.getByText('Saved “Stale investigation”.')).toBeVisible()

  await page.getByRole('link', { name: 'Saved Searches' }).click()
  await page.getByRole('link', { name: 'Open Wire watch' }).click()
  await expect(page).toHaveURL(/\/search\/\?/)
  expect(Object.fromEntries([...new URL(page.url()).searchParams].sort())).toEqual(Object.fromEntries([...investigation.searchParams].sort()))
  await expect(heading(page)).toHaveText('3 matching articles over time', { timeout: 30_000 })

  await page.getByRole('link', { name: 'Saved Searches' }).click()
  await page.getByRole('button', { name: 'Rename Wire watch' }).click()
  await page.getByLabel('New name for Wire watch').fill('Harbor wire watch')
  await page.getByRole('button', { name: 'Save name' }).click()
  await expect(page.getByRole('link', { name: 'Open Harbor wire watch' })).toBeVisible()
  page.once('dialog', dialog => dialog.accept())
  await page.getByRole('button', { name: 'Delete Harbor wire watch' }).click()
  await expect(page.getByText('Harbor wire watch')).toHaveCount(0)
})

test('invalid saved search explains why it cannot be opened', async ({ page }) => {
  await login(page)
  await page.getByRole('link', { name: 'Saved Searches' }).click()
  await expect(page.getByText('Stale investigation')).toBeVisible()
  await expect(page.getByText(/This saved search can no longer be opened: .*Extra inputs are not permitted/)).toBeVisible()
  await expect(page.getByRole('link', { name: 'Open Stale investigation' })).toHaveCount(0)
  page.once('dialog', dialog => dialog.accept())
  await page.getByRole('button', { name: 'Delete Stale investigation' }).click()
  await expect(page.getByText('Stale investigation')).toHaveCount(0)
})
