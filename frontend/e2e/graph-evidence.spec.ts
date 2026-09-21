import { expect, test, type Page } from '@playwright/test'

async function login(page: Page) {
  await page.goto('/')
  await page.getByLabel('Username').fill('phase10c')
  await page.getByLabel('Password').fill('phase10c-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('link', { name: 'Saved Searches' })).toBeVisible()
}

// Runs after `relationships seed` has collected, processed and clustered the shared fixture articles.
test('graph edge evidence workflow discloses the articles and story behind an edge', async ({ page }) => {
  await login(page)
  await page.goto('/graph')

  const connection = page.getByRole('list', { name: 'Connections in this graph' }).getByRole('button', { name: /Barack Obama.*Microsoft|Microsoft.*Barack Obama/ })
  await expect(connection).toBeVisible({ timeout: 30_000 })
  const weight = Number((await connection.innerText()).match(/·\s*(\d+)\s*$/)?.[1])
  expect(weight).toBeGreaterThan(1)
  await connection.click()

  await expect(page).toHaveURL(/edge=/)
  const panel = page.getByRole('complementary', { name: 'Relationship evidence' })
  await expect(panel.getByRole('heading', { level: 3 })).toHaveText(/Barack Obama and Microsoft|Microsoft and Barack Obama/)
  await expect(panel.getByText(/co-occurrence, not a stated relationship/)).toBeVisible()
  // The evidence total is the same number the graph drew on the edge.
  await expect(panel.getByText(`${weight} articles · about 1 story (estimated)`)).toBeVisible()
  // One story link plus one link per evidence article (the seeded pair are two reports of one story).
  const evidenceLinks = panel.getByRole('link', { name: /^Barack Obama meets Microsoft/ })
  await expect(evidenceLinks).toHaveCount(weight + 1)

  await evidenceLinks.last().click()
  await expect(page).toHaveURL(/\/articles\/\?/)
  await page.getByRole('link', { name: 'Back to graph' }).click()
  await expect(page).toHaveURL(/edge=/)
  await expect(page.getByRole('complementary', { name: 'Relationship evidence' })).toBeVisible()
})
