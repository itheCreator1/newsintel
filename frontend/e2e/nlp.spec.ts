import { expect, test } from '@playwright/test'

async function login(page: import('@playwright/test').Page) {
  await page.goto('/')
  await page.getByLabel('Username').fill('phase5')
  await page.getByLabel('Password').fill('phase5-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
}

test('annotations refine search and stop words use revisioned settings', async ({ page }) => {
  await login(page)
  await page.getByRole('link', { name: 'Articles' }).click()
  await page.getByText('Fixture story', { exact: true }).first().click()
  await expect(page.getByText(/Detected language: en/)).toBeVisible({ timeout: 45_000 })
  await expect(page.getByRole('heading', { name: 'Annotations' })).toBeVisible()
  const keyword = page.locator('.annotation-group').filter({ hasText: 'Keywords' }).getByRole('link').first()
  await expect(keyword).toBeVisible()
  await keyword.click()
  await expect(page).toHaveURL(/keyword_id=/)
  await expect(page.getByText('Fixture story', { exact: true }).first()).toBeVisible({ timeout: 30_000 })

  await page.getByLabel('Detected language').fill('en')
  await page.getByLabel('Story country').fill('DE')
  await page.getByRole('button', { name: 'Search archive' }).click()
  await expect(page).toHaveURL(/language=en/)
  await expect(page).toHaveURL(/story_country=DE/)
  await page.getByLabel('Detected language').fill('')
  await page.getByLabel('Story country').fill('')
  await page.getByRole('button', { name: 'Search archive' }).click()
  await expect(page).not.toHaveURL(/story_country=/)

  await page.getByRole('link', { name: 'Settings' }).click()
  const words = page.getByLabel('English stop words')
  await expect(words).not.toHaveValue('', { timeout: 30_000 })
  await words.fill(`${await words.inputValue()}\nphasefiveterm`)
  await page.getByRole('button', { name: 'Save stop words' }).click()
  await expect(page.getByText(/Stop words saved as revision/)).toBeVisible()

  await page.getByRole('link', { name: 'Jobs' }).click()
  await expect(page.getByRole('heading', { name: 'NLP processing' })).toBeVisible()
  await expect(page.getByText(/entities · disabled/)).toBeVisible()
})
