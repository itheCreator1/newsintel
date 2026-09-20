import { expect, test, type Page } from '@playwright/test'

async function login(page: Page) {
  await page.goto('/')
  await page.getByLabel('Username').fill('phase13c')
  await page.getByLabel('Password').fill('phase13c-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('link', { name: 'Saved Searches' })).toBeVisible()
}

// Runs after `relationships seed`: "Relationships Wire" is a Greek feed with three articles and
// "Relationships Daily" a US one with two. Nothing in the fixtures names a country, so only the source
// role has data, which also shows the empty story role. The fixture articles are dated 7-11 September
// 2026, so the widest window keeps them in range for longest.
test('map workflow separates the location roles and refines a search from a selected country', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await login(page)
  await page.getByRole('link', { name: 'Map', exact: true }).click()
  await expect(page).toHaveURL(/\/map\/$/)
  await page.getByLabel('Window').selectOption('365')

  // The story role is the default and says what it is missing rather than drawing nothing silently.
  await expect(page.getByText(/articles in the last 365 days (have|has) a story country/)).toBeVisible()

  await page.getByLabel('Location role').selectOption('source')
  await expect(page).toHaveURL(/role=source&days=365/)
  await expect(page.getByText(/of \d+ articles in the last 365 days have a source country/)).toBeVisible()
  await expect(page.getByRole('img', { name: /Map of articles by source country/ })).toBeVisible()
  await expect(page.locator('.geo-chart canvas')).toHaveCount(1) // the real map drew, not just the table
  const greece = page.getByRole('row', { name: /^Greece/ })
  await expect(greece.getByRole('cell').nth(1)).toHaveText('3')
  await expect(greece.getByRole('cell').nth(3)).toHaveText('1') // one feed
  await expect(page.getByRole('row', { name: /^United States/ }).getByRole('cell').nth(1)).toHaveText('2')

  await greece.getByRole('button', { name: 'Greece' }).click()
  await expect(page).toHaveURL(/role=source&days=365&country=GR/)
  const panel = page.getByRole('region', { name: 'Greece' })
  await expect(panel.getByText(/^3 articles/)).toBeVisible()
  await expect(panel.locator('a[href^="/articles/?article="]')).toHaveCount(3)

  // Selecting a country refines an ordinary search on the field that matches the role shown.
  await panel.getByRole('link', { name: 'Search these articles' }).click()
  await expect(page).toHaveURL(/\/search\/\?country=GR&after=/)
  await expect(page.locator('.search-result')).toHaveCount(3, { timeout: 60_000 })
  await page.goBack()
  await expect(page.getByRole('region', { name: 'Greece' })).toBeVisible()

  // The URL alone reproduces the view.
  const url = page.url()
  await page.goto(url)
  await expect(page.getByLabel('Location role')).toHaveValue('source')
  await expect(page.getByRole('region', { name: 'Greece' }).getByText(/^3 articles/)).toBeVisible()
  expect(errors).toEqual([])
})
