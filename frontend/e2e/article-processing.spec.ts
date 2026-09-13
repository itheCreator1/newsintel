import { expect, test } from '@playwright/test'

test('login, add a feed, ingest, extract, and open article detail', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('Username').fill('phase3')
  await page.getByLabel('Password').fill('phase3-password')
  await page.getByRole('button', { name: 'Sign in' }).click()

  await page.getByRole('link', { name: 'Sources' }).click()
  await page.getByLabel('Name').fill('Browser fixture')
  await page.getByLabel('Feed URL').fill('http://fixture/e2e-feed.xml')
  await page.getByLabel('Collection mode').first().selectOption('full_text_html')
  await page.getByRole('button', { name: 'Add source' }).click()
  await expect(page.getByText('Browser fixture')).toBeVisible()
  await page
    .locator('.source-row')
    .filter({ hasText: 'Browser fixture' })
    .getByRole('button', { name: 'Poll now' })
    .click()

  await expect
    .poll(async () => {
      const response = await page.request.get('/api/v1/articles')
      const body = await response.json()
      return body.items.some((item: { title: string }) => item.title === 'Fixture story')
    }, { timeout: 45_000 })
    .toBe(true)

  await page.getByRole('link', { name: 'Articles' }).click()
  await expect(page.getByText('Fixture story').first()).toBeVisible({ timeout: 45_000 })
  await page.getByText('Fixture story').first().click()
  await expect(page.getByText(/first readable fixture article/)).toBeVisible({ timeout: 45_000 })
  await expect(page.getByText(/HTML retained/)).toBeVisible()
})
