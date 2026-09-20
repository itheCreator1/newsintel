import { expect, test, type Page } from '@playwright/test'

async function login(page: Page) {
  await page.goto('/')
  await page.getByLabel('Username').fill('phase11d')
  await page.getByLabel('Password').fill('phase11d-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('link', { name: 'Watchlist' })).toBeVisible()
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

type Monitor = { id: string; name: string; evaluated_through: string | null; unseen_article_count: number }
const monitors = (page: Page) => apiJson<{ items: Monitor[] }>(page, '/monitors').then(result => result.items)

// The worker runs monitors every 5 s with a 10 s settle lag (docker/compose.e2e.yaml), so waits are for a few evaluation ticks.
test('monitor workflow watches a search, shows new articles, marks them seen and manages the monitor', async ({ page }) => {
  test.setTimeout(240_000)
  await login(page)

  // Watch before anything is collected: the first evaluation baselines, so only later articles count as new.
  await page.goto('/search?q=Harbor')
  await page.getByLabel('Monitor name').fill('Harbor watch')
  await page.getByRole('button', { name: 'Watch search' }).click()
  await expect(page.getByText('Watching “Harbor watch”.')).toBeVisible()
  await expect.poll(async () => (await monitors(page))[0]?.evaluated_through, { timeout: 60_000, intervals: [1_000] }).not.toBeNull()

  await apiJson(page, '/feeds', { method: 'POST', body: { name: 'Harbor Wire', url: 'http://fixture/investigations-wire.xml', source_country: 'GR' } })
  await apiJson(page, '/feeds', { method: 'POST', body: { name: 'Harbor Daily', url: 'http://fixture/investigations-daily.xml', source_country: 'US' } })
  await expect.poll(async () => (await monitors(page))[0]?.unseen_article_count, { timeout: 150_000, intervals: [2_000] }).toBe(6)

  await page.goto('/monitors')
  const row = page.getByRole('article', { name: 'Harbor watch' })
  await expect(row).toHaveAttribute('data-unseen', 'true')
  await expect(row.getByText(/^6 new articles/)).toBeVisible()
  await row.getByRole('link', { name: 'Open Harbor watch' }).click()
  await expect(page).toHaveURL(/\/monitors\/\?id=/)
  await expect(page.getByRole('heading', { level: 3, name: 'Harbor watch' })).toBeVisible()
  await expect(page.locator('main article')).toHaveCount(6)

  // Both feeds are new to this monitor; entities and stories need NER and clustering, which this stack does not run.
  const changes = page.getByRole('region', { name: 'What changed' })
  await expect(changes.getByText('6 new articles')).toBeVisible()
  await expect(changes.getByRole('link', { name: /^New source: Harbor (Wire|Daily) \(\d+ articles?\)$/ })).toHaveCount(2)
  await expect(changes.locator('a[href^="/articles/"]').first()).toBeVisible()

  await page.getByRole('button', { name: 'Mark as seen (6)' }).click()
  await expect(changes.getByText('No changes since you last looked.')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('Nothing new since you last looked.')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('Nothing new', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Recent matches' }).click()
  await expect(page).toHaveURL(/scope=recent/)
  await expect(page.locator('main article')).toHaveCount(6)

  await page.getByRole('button', { name: 'Pause monitor' }).click()
  await expect(page.getByText('Paused', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Resume monitor' }).click()
  await expect(page.getByRole('button', { name: 'Pause monitor' })).toBeVisible()

  await page.getByRole('button', { name: 'Rename monitor' }).click()
  await page.getByLabel('New name for Harbor watch').fill('Harbor desk')
  await page.getByRole('button', { name: 'Save name' }).click()
  await expect(page.getByRole('heading', { level: 3, name: 'Harbor desk' })).toBeVisible()

  page.on('dialog', dialog => dialog.accept())
  await page.getByRole('button', { name: 'Delete monitor' }).click()
  await expect(page).toHaveURL(/\/monitors\/$/)
  await expect(page.getByText(/No monitors yet/)).toBeVisible()
  expect(await monitors(page)).toEqual([])
})
