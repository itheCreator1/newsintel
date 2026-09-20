import { expect, test, type Page } from '@playwright/test'

async function login(page: Page) {
  await page.goto('/')
  await page.getByLabel('Username').fill('phase10b')
  await page.getByLabel('Password').fill('phase10b-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('link', { name: 'Saved Searches' })).toBeVisible()
}

// Runs after `relationships seed` has collected and clustered the shared fixture articles.
test('entity dossier workflow opens from an article and follows co-occurrence', async ({ page }) => {
  await login(page)
  const articleId = await page.evaluate(async () => {
    const items: { id: string; title: string }[] = (await (await fetch('/api/v1/articles?limit=100')).json()).items
    return items.find(item => item.title.startsWith('Barack Obama meets Microsoft'))?.id
  })
  expect(articleId).toBeTruthy()

  await page.goto(`/articles?article=${articleId}`)
  await page.getByRole('link', { name: 'Open dossier for Barack Obama' }).click({ timeout: 30_000 })
  await expect(page).toHaveURL(/\/entities\/\?id=/)
  await expect(page.getByRole('heading', { level: 3, name: 'Barack Obama' })).toBeVisible()
  await expect(page.getByText('Aliases unavailable')).toBeVisible()
  await expect(page.getByRole('link', { name: /^Barack Obama meets Microsoft/ }).first()).toBeVisible()
  await expect(page.getByText('Recent stories')).toBeVisible()

  await page.getByRole('link', { name: /^Microsoft \(ORG\)/ }).click()
  await expect(page.getByRole('heading', { level: 3, name: 'Microsoft' })).toBeVisible()

  await page.goBack()
  await page.getByRole('link', { name: /^Barack Obama meets Microsoft/ }).first().click()
  await expect(page).toHaveURL(/\/articles\/\?/)
  await expect(page.getByRole('link', { name: 'Back to entity' })).toBeVisible()
})
