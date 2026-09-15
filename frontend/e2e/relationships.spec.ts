import { expect, test, type Page } from '@playwright/test'

async function login(page: Page) {
  await page.goto('/')
  await page.getByLabel('Username').fill('phase7')
  await page.getByLabel('Password').fill('phase7-password')
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

const KNOWN_TITLES = [
  'Barack Obama meets Microsoft executives in Washington',
  'United Nations envoy visits Brussels for a climate summit',
  'Town council opens a renovated library branch',
]

const heading = (page: Page) => page.getByRole('heading', { level: 3, name: /matching articles over time|Timeline/ })

test('relationships seed collects five fixture articles and settles clustering', async ({ page }) => {
  await login(page)
  await apiJson(page, '/feeds', { method: 'POST', body: { name: 'Relationships Wire', url: 'http://fixture/relationships-wire.xml', source_country: 'GR' } })
  await apiJson(page, '/feeds', { method: 'POST', body: { name: 'Relationships Daily', url: 'http://fixture/relationships-daily.xml', source_country: 'US' } })

  await expect.poll(async () => {
    const result = await apiJson<{ items: { title: string }[] }>(page, '/articles?limit=100')
    return result.items.filter(item => KNOWN_TITLES.includes(item.title)).length
  }, { timeout: 60_000, intervals: [1_000] }).toBe(5)

  // Clustering only follows the entities NLP processor, so wait for both matched pairs to
  // actually land in a cluster, not just for the job queues to drain (which would also be
  // true before any clustering job ever ran).
  await expect.poll(async () => {
    const status = await apiJson<{ queued: number; running: number; retrying: number; clustered_articles: number }>(page, '/clustering/status')
    return status.queued + status.running + status.retrying === 0 && status.clustered_articles >= 4
  }, { timeout: 120_000, intervals: [1_000] }).toBe(true)
})

test('relationships workflow links search, the cluster, article detail, and the entity graph', async ({ page }) => {
  await login(page)

  await page.goto('/search?q=Microsoft')
  await expect(heading(page)).toHaveText('2 matching articles over time', { timeout: 30_000 })
  await expect(page.locator('.search-result')).toHaveCount(2)
  const reportedByLinks = page.getByRole('link', { name: 'Also reported by 1 other source' })
  await expect(reportedByLinks).toHaveCount(2)

  await reportedByLinks.first().click()
  await expect(page).toHaveURL(/\/clusters\//)
  await expect(page.getByRole('heading', { level: 3 })).toHaveText('2 articles · 2 sources')
  const memberRows = page.locator('.search-result')
  await expect(memberRows).toHaveCount(2)
  await expect(page.getByText('Relationships Wire')).toBeVisible()
  await expect(page.getByText('Relationships Daily')).toBeVisible()

  await memberRows.first().locator('.result-open').click()
  await expect(page).toHaveURL(/\/articles\?/)
  const relatedGroup = page.locator('.story .annotation-group')
  await expect(relatedGroup.getByText('Related articles')).toBeVisible({ timeout: 30_000 })
  await expect(relatedGroup.getByRole('link')).toHaveCount(1)

  await page.goBack()
  await expect(page).toHaveURL(/\/clusters\//)
  await page.getByRole('link', { name: 'Search within this story' }).click()
  await expect(page).toHaveURL(/story_cluster_id=/)
  await expect(heading(page)).toHaveText('2 matching articles over time', { timeout: 30_000 })
  await expect(page.locator('.search-result')).toHaveCount(2)

  await page.goto('/graph')
  const graphNode = page.getByRole('button', { name: /^Barack Obama \(PERSON\)/ })
  await expect(graphNode).toBeVisible({ timeout: 30_000 })
  await graphNode.click()
  await expect(page).toHaveURL(/focus=/)
  const panel = page.getByRole('complementary', { name: 'Entity details' })
  await expect(panel.getByRole('heading', { name: 'Barack Obama' })).toBeVisible()
  await expect(panel.getByText('Connected entities')).toBeVisible()
  await expect(panel.getByRole('button', { name: 'Microsoft' })).toBeVisible()

  await panel.getByRole('link', { name: 'Search articles with Barack Obama' }).click()
  await expect(page).toHaveURL(/\/search\?/)
  await expect(page).toHaveURL(/entity_id=/)
})
