import { expect, test, type Page } from '@playwright/test'

async function login(page: Page) {
  await page.goto('/')
  await page.getByLabel('Username').fill('phase13b')
  await page.getByLabel('Password').fill('phase13b-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('link', { name: 'Saved Searches' })).toBeVisible()
}

// Runs after `relationships seed`: "Relationships Wire" and "Relationships Daily" share two stories.
// The fixture articles are dated 7-11 September 2026, so the widest window keeps them in range for longest.
test('compare workflow moves from a source dossier to shared evidence and back', async ({ page }) => {
  await login(page)
  await page.getByRole('link', { name: 'Sources', exact: true }).click()
  await page.getByRole('link', { name: 'Dossier for Relationships Wire' }).click()
  await page.getByRole('link', { name: 'Compare with another source' }).click()
  await expect(page).toHaveURL(/\/compare\/\?kind=source&a=/)
  await expect(page.getByLabel('Source A')).not.toHaveValue('')

  await page.getByLabel('Window').selectOption('365')
  await page.getByLabel('Source B').selectOption({ label: 'Relationships Daily' })
  await expect(page).toHaveURL(/kind=source&a=.+&b=.+&days=365/)
  await expect(page.getByRole('heading', { name: 'Relationships Wire', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Relationships Daily', exact: true })).toBeVisible()
  await expect(page.getByLabel('Stories overlap')).toContainText(/^Stories\s*[1-9]\d* of \d+ stories in either set appear in both · Jaccard \d\.\d\d/)
  await expect(page.getByLabel('Articles overlap')).toContainText(/of \d+ articles in either set appear in both/)

  // The stories both sources carry, then one of them and back with the comparison intact.
  await page.getByLabel('Stories overlap').getByRole('button', { name: /^Both: / }).click()
  await page.getByRole('button', { name: 'Stories', exact: true }).click()
  await page.locator('a[href^="/clusters/?id="]').first().click()
  await expect(page).toHaveURL(/\/clusters\/\?id=/)
  await page.goBack()
  await expect(page.getByLabel('Source B')).toHaveValue(/.+/)
  await expect(page.getByLabel('Stories overlap')).toBeVisible()

  // An article carries the way back to the comparison. Two sources rarely carry the same article, so
  // the shared part is empty; the articles only the first carries are listed instead.
  await page.getByRole('button', { name: 'Articles', exact: true }).click()
  await page.getByLabel('Show').selectOption('a')
  await page.locator('a[href^="/articles/?article="]').first().click()
  await page.getByRole('link', { name: 'Back to comparison' }).click()
  await expect(page).toHaveURL(/\/compare\/\?kind=source&a=.+&b=.+&days=365/)

  // The URL alone reproduces the view.
  const url = page.url()
  await page.goto(url)
  await expect(page.getByRole('heading', { name: 'Relationships Daily', exact: true })).toBeVisible()
  await expect(page.getByLabel('Stories overlap')).toContainText('appear in both')
})
