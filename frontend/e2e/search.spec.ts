import { expect, test } from '@playwright/test'

test('search restores URL state, opens detail, and reports unavailability', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('Username').fill('phase4')
  await page.getByLabel('Password').fill('phase4-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await page.getByRole('link', { name: 'Search', exact: true }).click()
  await page.getByLabel('Query', { exact: true }).fill('Fixture AND story')
  await page.getByLabel('Sort').selectOption('newest')
  await page.getByRole('button', { name: 'Search archive' }).click()
  await expect(page).toHaveURL(/q=Fixture/)
  await expect(page).toHaveURL(/sort=newest/)
  await expect(page.getByText('Fixture story', { exact: true }).first()).toBeVisible({ timeout: 30_000 })
  await page.reload()
  await expect(page.getByLabel('Query', { exact: true })).toHaveValue('Fixture AND story')
  await page.getByText('Fixture story', { exact: true }).first().click()
  await expect(page.getByText(/first readable fixture article/)).toBeVisible({ timeout: 30_000 })
})
