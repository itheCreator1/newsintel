import { expect, test, type Page } from '@playwright/test'

async function login(page: Page, user = 'phase14b') {
  await page.goto('/')
  await page.getByLabel('Username').fill(user)
  await page.getByLabel('Password').fill(`${user}-password`)
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

// The suggestion pairs live in their own language ("zz"), seeded by infra/test-e2e.sh, so no other
// spec sees them and the queue holds exactly these two.
test('authority file workflow approves and rejects suggested duplicates', async ({ page }) => {
  await login(page, 'phase14c')
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Authority file' }).click()
  await expect(page).toHaveURL(/\/authorities\/$/)
  await page.getByLabel('Language').fill('zz')

  const queue = page.getByRole('list', { name: 'Maybe the same?' })
  await expect(queue.getByRole('listitem')).toHaveCount(2)
  await expect(queue.getByText('Is “NAC” the same as “North Atlantic Council”?')).toBeVisible()
  await expect(queue.getByText('Is “J. Tarr” the same as “Jon Tarr”?')).toBeVisible()

  await page.getByRole('button', { name: 'Same entity: merge NAC into North Atlantic Council' }).click()
  await expect(page.getByText('NAC is now a name of North Atlantic Council; its articles move over shortly.')).toBeVisible()
  await expect(queue.getByRole('listitem')).toHaveCount(1)
  await page.getByRole('button', { name: 'Different: keep J. Tarr apart from Jon Tarr' }).click()
  await expect(page.getByText('J. Tarr and Jon Tarr are recorded as different.')).toBeVisible()
  await expect(page.getByText('No likely duplicates right now.')).toBeVisible()

  await page.getByLabel('Find a name').fill('north')
  const file = page.getByRole('list', { name: 'Authority file' })
  await expect(file.getByRole('listitem')).toHaveCount(1)
  await expect(file.getByText('ORG · zz · 1 other name')).toBeVisible()
  await file.getByRole('link', { name: 'North Atlantic Council' }).click()
  await expect(page.getByRole('list', { name: 'Other names' }).getByText('NAC')).toBeVisible()

  await page.goBack()
  const history = page.getByRole('list', { name: 'Recent changes' })
  await expect(history.getByRole('listitem').first()).toContainText(/(J\. Tarr and Jon Tarr|Jon Tarr and J\. Tarr) recorded as different/)
  await expect(history.getByRole('listitem').nth(1)).toContainText('NAC merged into North Atlantic Council')
})
