import { expect, test, type Page } from '@playwright/test'

async function login(page: Page) {
  await page.goto('/')
  await page.getByLabel('Username').fill('phase14b')
  await page.getByLabel('Password').fill('phase14b-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('link', { name: 'Saved Searches' })).toBeVisible()
}

async function entityId(page: Page, name: string): Promise<string> {
  return page.evaluate(async name => {
    const items: { id: string; text: string }[] = (await (await fetch(`/api/v1/nlp/entities?q=${encodeURIComponent(name)}`)).json()).items
    return items.find(item => item.text === name)?.id ?? ''
  }, name)
}

async function json<T>(page: Page, path: string): Promise<T> {
  return page.evaluate(async path => (await fetch(`/api/v1${path}`)).json(), path)
}

const searched = (page: Page, id: string) =>
  json<{ items: { article_id: string }[] }>(page, `/search?entity_id=${id}&limit=100`).then(page => page.items.map(item => item.article_id).sort())
const linked = (page: Page, id: string) =>
  json<{ items: { id: string }[] }>(page, `/entities/${id}/articles?limit=100`).then(page => page.items.map(item => item.id).sort())

// Runs after `relationships seed`; it splits what it merged, so the specs after it see the same data.
test('authority merge workflow merges, keeps old ids working, and splits back', async ({ page }) => {
  test.setTimeout(180_000)
  await login(page)
  const nations = await entityId(page, 'United Nations')
  const microsoft = await entityId(page, 'Microsoft')
  expect(nations).toBeTruthy()
  expect(microsoft).toBeTruthy()
  const articles = await linked(page, nations)
  expect(articles.length).toBeGreaterThan(0)
  expect(await searched(page, nations)).toEqual(articles)

  await page.goto(`/entities?id=${nations}`)
  await page.getByRole('button', { name: 'Merge with…' }).click()
  await page.getByLabel('Find the entity to merge into').fill('Microsoft')
  await page.getByRole('button', { name: 'Microsoft (ORG)', exact: true }).click()
  await page.getByRole('button', { name: 'Merge into Microsoft' }).click()
  await expect(page).toHaveURL(new RegExp(`/entities/\\?id=${microsoft}`))
  await expect(page.getByRole('list', { name: 'Other names' }).getByText('United Nations')).toBeVisible()

  // The old id opens the root, and searching by it finds the same articles throughout the run.
  await page.goto(`/entities?id=${nations}`)
  await expect(page.getByText('Opened from a name merged into this entity.')).toBeVisible()
  await expect(page.getByRole('heading', { level: 3, name: 'Microsoft' })).toBeVisible()
  await expect.poll(() => linked(page, microsoft), { timeout: 90_000 }).toEqual(expect.arrayContaining(articles))
  expect(await searched(page, nations)).toEqual(expect.arrayContaining(articles))

  await page.goto(`/entities?id=${microsoft}`)
  await page.getByRole('button', { name: 'Split United Nations' }).click()
  await expect(page.getByText('United Nations is its own entity again; its articles move back shortly.')).toBeVisible()
  await expect.poll(() => linked(page, nations), { timeout: 90_000 }).toEqual(articles)
  await expect.poll(() => searched(page, nations), { timeout: 90_000 }).toEqual(articles)
  await page.goto(`/entities?id=${nations}`)
  await expect(page.getByRole('heading', { level: 3, name: 'United Nations' })).toBeVisible()
  await expect(page.getByText('Opened from a name merged into this entity.')).toHaveCount(0)
})
