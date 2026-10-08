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

// Runs after `authority merge workflow` (a see-also link would block its merge) and removes both
// links it adds, so the specs after it see the same data. The fixture has no renamed company, so
// Microsoft stands in for an earlier name of the United Nations: what is checked is the mechanics.
test('see also workflow links entities and follows the link in search and the graph', async ({ page }) => {
  test.setTimeout(120_000)
  await login(page, 'phase14d')
  const microsoft = await entityId(page, 'Microsoft')
  const nations = await entityId(page, 'United Nations')
  const brussels = await entityId(page, 'Brussels')
  expect(microsoft && nations && brussels).toBeTruthy()

  // From an article: two of its entities, with the article recorded as the source.
  const summit = (await json<{ items: { id: string; title: string }[] }>(page, '/articles?limit=100')).items
    .find(item => item.title === 'United Nations envoy visits Brussels for a climate summit')!
  await page.goto(`/articles?article=${summit.id}`)
  await page.getByRole('button', { name: 'Link two entities' }).click()
  const form = page.getByRole('form', { name: 'Link entities from this article' })
  await form.getByLabel('Entity', { exact: true }).selectOption(nations)
  await form.getByLabel('Link type').selectOption('related')
  await form.getByLabel('Linked entity').selectOption(brussels)
  await form.getByRole('button', { name: 'Save link' }).click()
  await expect(page.getByRole('status')).toHaveText('Linked United Nations to Brussels, with this article as the source.')

  // From the dossier: a later name, found through the linked-entity picker.
  await page.goto(`/entities?id=${microsoft}`)
  await page.getByRole('button', { name: 'Add link' }).click()
  await page.getByLabel('Link type').selectOption('later_name')
  await page.getByLabel('Find the linked entity').fill('United Nations')
  await page.getByRole('button', { name: 'United Nations (ORG)', exact: true }).click()
  await page.getByRole('button', { name: 'Save link' }).click()
  await expect(page.getByRole('list', { name: 'See also' })).toHaveText(/Later name: United Nations/)

  await page.goto(`/entities?id=${nations}`)
  const links = page.getByRole('list', { name: 'See also' })
  await expect(links.getByRole('listitem')).toHaveCount(2)
  await expect(links).toContainText('Earlier name: Microsoft')
  await expect(links).toContainText('Related: Brussels · Source: United Nations envoy visits Brussels for a climate summit')

  // Search follows the link only when asked: the earlier name's articles join the later name's.
  await page.goto(`/search?entity_id=${nations}`)
  await expect(page.locator('.search-result')).toHaveCount(2, { timeout: 30_000 })
  await page.getByRole('group', { name: /^Follow see-also links/ }).getByRole('checkbox', { name: 'Earlier and later names' }).check()
  await page.getByRole('button', { name: 'Search archive' }).click()
  await expect(page).toHaveURL(/entity_expand=names/)
  await expect(page.locator('.search-result')).toHaveCount(4)
  await expect(page.locator('.search-result').filter({ hasText: 'Barack Obama meets Microsoft executives in Washington' })).toHaveCount(2)
  await expect(page.getByRole('button', { name: 'Remove Entity links: earlier and later names' })).toBeVisible()

  // The graph draws stated links only when switched on, beside co-occurrence.
  await page.goto('/graph')
  const toggle = page.getByRole('button', { name: 'Stated links' })
  await expect(toggle).toHaveAttribute('aria-pressed', 'false')
  await expect(page.getByRole('list', { name: 'Stated links in this graph' })).toHaveCount(0)
  await toggle.click()
  await expect(page).toHaveURL(/stated=1/)
  await expect(page.getByRole('list', { name: 'Stated links in this graph' })).toContainText('Microsoft · later name · United Nations')
  await expect(page.getByText('Dotted purple lines are see-also links you stated; they never count as co-occurrence.')).toBeVisible()

  // Clean up, so later specs see no links.
  await page.goto(`/entities?id=${nations}`)
  await page.getByRole('button', { name: 'Remove link to Microsoft' }).click()
  await page.getByRole('button', { name: 'Remove link to Brussels' }).click()
  await expect(page.getByText('No see-also links yet.')).toBeVisible()
})
