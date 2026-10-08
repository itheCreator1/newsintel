import { expect, test, type Page } from '@playwright/test'

async function login(page: Page) {
  await page.goto('/')
  await page.getByLabel('Username').fill('phase14a')
  await page.getByLabel('Password').fill('phase14a-password')
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

const TITLE = 'Ο Αλέξης Τσίπρας συναντήθηκε με τον Κυριάκο Μητσοτάκη στην Αθήνα'

// Runs in the graph group, the only one whose stack has the NER image with the real Greek model,
// after every spec that counts the shared fixture articles.
test('greek entities workflow finds names in a Greek article', async ({ page }) => {
  await login(page)

  await page.goto('/settings')
  const greekSwitch = page.getByRole('switch', { name: /^Greek entities/ })
  await expect(greekSwitch).toBeEnabled({ timeout: 30_000 })
  // The switch follows the saved setting, so it flips only once the server has answered:
  // click and wait for that, rather than check(), which expects the box to change at once.
  await greekSwitch.click()
  await expect(page.getByText(/^Greek entities are on\./)).toBeVisible({ timeout: 30_000 })
  await expect(greekSwitch).toBeChecked()

  await apiJson(page, '/feeds', { method: 'POST', body: { name: 'Greek Wire', url: 'http://fixture/greek-wire.xml', source_country: 'GR' } })
  let articleId = ''
  await expect.poll(async () => {
    const result = await apiJson<{ items: { id: string; title: string }[] }>(page, '/articles?limit=100')
    articleId = result.items.find(item => item.title === TITLE)?.id ?? ''
    return articleId
  }, { timeout: 60_000, intervals: [1_000] }).not.toBe('')

  await expect.poll(async () => {
    const annotations = await apiJson<{ entities: { text: string; normalized_text: string; entity_type: string }[] }>(page, `/articles/${articleId}/annotations`)
    return annotations.entities.map(entity => `${entity.entity_type}:${entity.normalized_text}`).sort()
  }, { timeout: 120_000, intervals: [1_000] }).toEqual(['GPE:αθηνα', 'PERSON:αλεξη τσιπρα', 'PERSON:κυριακο μητσοτακη'])

  await page.goto(`/articles?article=${articleId}`)
  await page.getByRole('link', { name: 'Open dossier for Αλέξης Τσίπρας' }).click({ timeout: 30_000 })
  await expect(page).toHaveURL(/\/entities\/\?id=/)
  await expect(page.getByRole('heading', { level: 3, name: 'Αλέξης Τσίπρας' })).toBeVisible()
})
