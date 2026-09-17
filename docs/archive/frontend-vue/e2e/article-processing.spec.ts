import { expect, test } from '@playwright/test'

async function login(page: import('@playwright/test').Page) {
  await page.goto('/')
  await page.getByLabel('Username').fill('phase3')
  await page.getByLabel('Password').fill('phase3-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
}

test('failure and retry workflow', async ({ page }) => {
  await login(page)

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
  await expect(page.getByText('Fixture story', { exact: true }).first()).toBeVisible({ timeout: 45_000 })
  await page.getByText('Fixture story', { exact: true }).first().click()
  await expect(page.getByText(/first readable fixture article/)).toBeVisible({ timeout: 45_000 })
  await expect(page.getByText(/HTML retained/)).toBeVisible()

  await page.getByRole('link', { name: 'Jobs' }).click()
  const failed = page.locator('.job-row').filter({ hasText: 'Recoverable fixture story' })
  await expect(failed.getByText('failed', { exact: true })).toBeVisible({ timeout: 45_000 })
  await expect(failed.getByText(/http_transient/)).toBeVisible()
  await failed.getByRole('button', { name: 'Retry' }).click()
  await expect(page.getByText('Retry scheduled.')).toBeVisible()

  await page.getByRole('link', { name: 'Articles' }).click()
  await page.getByText('Recoverable fixture story').first().click()
  await expect(page.getByText(/This changed fixture article/)).toBeVisible({ timeout: 45_000 })
})

test('retained detail survives worker recreation', async ({ page }) => {
  await login(page)
  await page.getByRole('link', { name: 'Articles' }).click()
  await page.getByText('Fixture story', { exact: true }).first().click()
  await expect(page.getByText(/first readable fixture article/)).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText(/HTML retained/)).toBeVisible()
})
