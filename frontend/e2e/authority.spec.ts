import { expect, test, type Page } from '@playwright/test'

async function login(page: Page, user = 'phase14b') {
  await page.goto('/')
  await page.getByLabel('Username').fill(user)
  await page.getByLabel('Password').fill(`${user}-password`)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: /^Investigations/ })).toBeVisible()
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

  await page.goto(`/entities?id=${nations}&tab=catalogue`)
  await page.getByRole('button', { name: 'Edit', exact: true }).click()
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

  await page.goto(`/entities?id=${microsoft}&tab=catalogue`)
  await page.getByRole('button', { name: 'Edit', exact: true }).click()
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
  const tabs = page.getByRole('tablist', { name: 'Authority file sections' })
  await expect(tabs.getByRole('tab', { name: /^Maybe the same\?/ })).toHaveText('Maybe the same?2')
  await tabs.getByRole('tab', { name: /^Maybe the same\?/ }).click()
  await expect(page).toHaveURL(/tab=suggestions/)

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

  await tabs.getByRole('tab', { name: 'Names' }).click()
  await page.getByLabel('Find a name').fill('north')
  const file = page.getByRole('table', { name: 'Authority file' })
  await expect(file.getByRole('row')).toHaveCount(2)
  await expect(file.getByRole('row').nth(1).getByRole('cell')).toHaveText(['', 'North Atlantic Council', 'Organization', 'zz', '0', '1', 'Provisional', '—'])
  await file.getByRole('button', { name: 'North Atlantic Council' }).click()
  const panel = page.getByRole('complementary', { name: 'Edit North Atlantic Council' })
  await expect(panel.getByRole('list', { name: 'Other names' }).getByText('NAC')).toBeVisible()
  await panel.getByRole('button', { name: 'Close the editor' }).click()
  await expect(panel).toHaveCount(0)

  await tabs.getByRole('tab', { name: 'Changes' }).click()
  const history = page.getByRole('list', { name: 'Recent changes' })
  await expect(history.getByRole('listitem').first()).toContainText(/(J\. Tarr and Jon Tarr|Jon Tarr and J\. Tarr) recorded as different/)
  await expect(history.getByRole('listitem').nth(1)).toContainText('NAC merged into North Atlantic Council')
})

// "AI" in three types, a weekday NER took for a name and two spellings of one company, in a
// language of their own (seeded by infra/test-e2e.sh), so no other spec sees them.
test('authority types workflow claims a name for every type, retypes, hides and merges in bulk', async ({ page }) => {
  test.setTimeout(180_000)
  await login(page, 'phase16a')
  const roots = (q: string, hidden = false) =>
    json<{ items: { display_name: string }[] }>(page, `/authorities?language=zy&q=${q}${hidden ? '&hidden=true' : ''}`).then(page => page.items.length)
  await page.goto('/authorities?language=zy&q=ai')
  const file = page.getByRole('table', { name: 'Authority file' })
  await expect(file.getByRole('row')).toHaveCount(4)

  // Bring every type of "AI" under the organization, then make it a topic.
  await file.getByRole('row').filter({ hasText: 'Organization' }).getByRole('button', { name: 'AI' }).click()
  const panel = page.getByRole('complementary', { name: 'Edit AI' })
  await expect(panel.getByRole('list', { name: 'Same name, other types' }).getByRole('listitem')).toHaveCount(2)
  await panel.getByRole('button', { name: 'Bring every type of “AI” here' }).click()
  await expect(panel.getByText('Claimed “AI”: 2 names join shortly, and every “AI” found later joins too.')).toBeVisible()
  await expect(panel.getByRole('list', { name: 'Claimed names' })).toContainText('“ai”: every type joins here')
  await expect.poll(() => roots('ai'), { timeout: 90_000 }).toBe(1)
  await panel.getByLabel('Type').selectOption('TOPIC')
  await expect(panel.getByText('AI is now a Topic; search and the graph follow shortly.')).toBeVisible()
  await page.reload()
  await expect(file.getByRole('row')).toHaveCount(2)
  await expect(file.getByRole('row').nth(1).getByRole('cell').nth(2)).toHaveText('Topic')
  await expect(page.getByRole('complementary', { name: 'Edit AI' }).getByRole('list', { name: 'Other names' }).getByRole('listitem')).toHaveCount(2)

  // A weekday is not a name: hidden, it leaves the list until asked for, and comes back.
  await page.goto('/authorities?language=zy&q=monday')
  await file.getByRole('button', { name: 'Monday' }).click()
  await page.getByRole('complementary', { name: 'Edit Monday' }).getByRole('button', { name: 'Hide as not a name' }).click()
  await expect(page.getByText('Hidden: left out of search, the graph and the suggestions.')).toBeVisible()
  await expect.poll(() => roots('monday'), { timeout: 30_000 }).toBe(0)
  await page.getByLabel('Hidden names only').check()
  await expect(file.getByRole('row').nth(1)).toContainText('MondayHidden')
  await page.getByRole('button', { name: 'Show again' }).click()
  await expect.poll(() => roots('monday'), { timeout: 30_000 }).toBe(1)

  // Two spellings of one company, merged in one go from the table.
  await page.goto('/authorities?language=zy&q=acme')
  await expect(file.getByRole('row')).toHaveCount(3)
  await page.getByLabel('Select all shown').check()
  const bar = page.getByRole('toolbar', { name: 'Bulk actions' })
  await expect(bar).toContainText('2 selected')
  await bar.getByLabel('Keep').selectOption({ label: 'Acme' })
  await bar.getByRole('button', { name: 'Merge 1 into Acme' }).click()
  await expect(page.getByText('1 name is merging into Acme; its articles move over shortly.')).toBeVisible()
  await expect.poll(() => roots('acme'), { timeout: 90_000 }).toBe(1)

  await page.goto('/authorities?tab=changes')
  const history = page.getByRole('list', { name: 'Recent changes' })
  await expect(history).toContainText('Acme Corp merged into Acme')
  await expect(history).toContainText('Monday: shown again')
  await expect(history).toContainText('Monday: hidden as not a name')
  await expect(history).toContainText('AI: type changed from Organization to Topic')
  await expect(history).toContainText('AI: claims every type of “ai”')
})

// Runs after `authority merge workflow` (a see-also link would block its merge) and removes both
// links it adds, so the specs after it see the same data. The fixture has no renamed company, so
// Microsoft stands in for an earlier name of the United Nations: what is checked is the mechanics.
test('see also workflow links entities and follows the link in search and the graph', async ({ page }) => {
  test.setTimeout(120_000)
  await login(page, 'phase14d')
  const microsoft = await entityId(page, 'Microsoft')
  expect(microsoft).toBeTruthy()

  // From an article: two of its entities, with the article recorded as the source.
  const summit = (await json<{ items: { id: string; title: string }[] }>(page, '/articles?limit=100')).items
    .find(item => item.title === 'United Nations envoy visits Brussels for a climate summit')!
  await page.goto(`/articles?article=${summit.id}`)
  // The ids the article itself names: a name search can also find a same-named entity of another type.
  const dossierId = async (name: string) =>
    new URL((await page.getByRole('link', { name: `Open dossier for ${name}` }).getAttribute('href'))!, 'http://n').searchParams.get('id')!
  const nations = await dossierId('United Nations')
  const brussels = await dossierId('Brussels')
  await page.getByRole('button', { name: 'Link two entities' }).click()
  const form = page.getByRole('form', { name: 'Link entities from this article' })
  await form.getByRole('combobox', { name: 'Entity', exact: true }).selectOption(nations)
  await form.getByLabel('Link type').selectOption('related')
  await form.getByLabel('Linked entity').selectOption(brussels)
  // A plain related link must say how the two relate.
  await form.getByLabel('Link note').fill('Host city of the climate summit')
  await form.getByRole('button', { name: 'Save link' }).click()
  await expect(page.getByRole('status')).toHaveText('Linked United Nations to Brussels, with this article as the source.')

  // From the dossier: a later name, found through the linked-entity picker.
  await page.goto(`/entities?id=${microsoft}&tab=relationships`)
  await page.getByRole('button', { name: 'Add link' }).click()
  await page.getByLabel('Link type').selectOption('later_name')
  await page.getByLabel('Find the linked entity').fill('United Nations')
  await page.getByRole('button', { name: 'United Nations (ORG)', exact: true }).click()
  await page.getByRole('button', { name: 'Save link' }).click()
  await expect(page.getByRole('list', { name: 'See also' })).toHaveText(/Later name: United Nations/)

  await page.goto(`/entities?id=${nations}&tab=relationships`)
  const links = page.getByRole('list', { name: 'See also' })
  await expect(links.getByRole('listitem')).toHaveCount(2)
  await expect(links).toContainText('Earlier name: Microsoft')
  await expect(links).toContainText('Related: Brussels · Host city of the climate summit · Source: United Nations envoy visits Brussels for a climate summit')

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
  await page.goto(`/entities?id=${nations}&tab=relationships`)
  await page.getByRole('button', { name: 'Remove link to Microsoft' }).click()
  await page.getByRole('button', { name: 'Remove link to Brussels' }).click()
  await expect(page.getByText('No see-also links yet.')).toBeVisible()
})
