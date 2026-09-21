import { expect, test, type Page } from '@playwright/test'

async function login(page: Page) {
  await page.goto('/')
  await page.getByLabel('Username').fill('phase13a')
  await page.getByLabel('Password').fill('phase13a-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('link', { name: 'Saved Searches' })).toBeVisible()
}

// Runs after `relationships seed`: "Relationships Wire" published both shared stories a day before "Relationships Daily".
// The fixture server shifts the articles to end yesterday; the widest window also covers a stale Compose stack.
test('source dossier workflow moves from the source list to stories, articles and entities', async ({ page }) => {
  await login(page)
  await page.getByRole('link', { name: 'Sources', exact: true }).click()
  await expect(page).toHaveURL(/\/sources\//)
  await page.getByRole('link', { name: 'Dossier for Relationships Wire' }).click()
  await expect(page).toHaveURL(/\/sources\/detail\/\?id=/)
  await expect(page.getByRole('heading', { name: 'Relationships Wire' })).toBeVisible()
  await expect(page.getByText('Healthy', { exact: true })).toBeVisible()

  await page.getByLabel('Window').selectOption('90')
  await expect(page.getByText('3 of 3 articles have a publish date')).toBeVisible()
  await expect(page.getByText(/^First in [1-9]\d* of \d+ shared stor/)).toBeVisible()
  await expect(page.getByRole('region', { name: 'Fetch history' }).getByText(/\d+ new \/ \d+ entries/).first()).toBeVisible()
  await expect(page.getByText('First to publish').first()).toBeVisible()

  // Story, then back to the same dossier.
  await page.locator('a[href^="/clusters/?id="]').first().click()
  await expect(page).toHaveURL(/\/clusters\/\?id=/)
  await page.goBack()
  await expect(page.getByRole('heading', { name: 'Relationships Wire' })).toBeVisible()

  // Article, then the labelled way back to the source.
  await page.locator('a[href^="/articles/?article="]').first().click()
  await expect(page).toHaveURL(/\/articles\/\?/)
  await page.getByRole('link', { name: 'Back to source' }).click()
  await expect(page).toHaveURL(/\/sources\/detail\/\?id=/)

  // Entity dossier, and from there the sources that cover it.
  await page.getByLabel('Window').selectOption('90')
  await page.locator('a[href^="/entities/?id="]').first().click()
  await expect(page).toHaveURL(/\/entities\/\?id=/)
  await page.locator('a[href^="/sources/detail/?id="]').first().click()
  await expect(page).toHaveURL(/\/sources\/detail\/\?id=/)
})
