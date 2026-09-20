import { expect, test, type Page } from '@playwright/test'

async function login(page: Page) {
  await page.goto('/')
  await page.getByLabel('Username').fill('phase12d')
  await page.getByLabel('Password').fill('phase12d-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('link', { name: 'Saved Searches' })).toBeVisible()
}

// Runs after `relationships seed` has collected and clustered the shared fixture articles; the scheduler
// then associates those clusters into events on its own (every 30 seconds).
test('event workflow moves from the list to stories, articles and entities', async ({ page }) => {
  await login(page)
  await expect.poll(
    () => page.evaluate(async () => (await (await fetch('/api/v1/events')).json()).items?.length ?? 0),
    { timeout: 120_000, intervals: [2_000] },
  ).toBeGreaterThan(0)

  await page.getByRole('link', { name: 'Events', exact: true }).click()
  await expect(page).toHaveURL(/\/events\//)
  await expect(page.locator('a[href^="/events/detail/"]').first()).toBeVisible()

  // Filters live in the URL and reach the API: nothing is superseded, and clearing the filter restores the list.
  await page.getByLabel('Status').selectOption('superseded')
  await expect(page).toHaveURL(/status=superseded/)
  await expect(page.getByText('No events match these filters.')).toBeVisible()
  await page.getByLabel('Status').selectOption('')
  await expect(page.locator('a[href^="/events/detail/"]').first()).toBeVisible()

  await page.locator('a[href^="/events/detail/"]').first().click()
  await expect(page).toHaveURL(/\/events\/detail\/\?id=/)
  await expect(page.getByRole('heading', { level: 3 }).first()).toBeVisible()
  await expect(page.getByRole('region', { name: 'Day details' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Stories' })).toBeVisible()
  await expect(page.getByText(/^Joined with score /).first()).toBeVisible()

  // Story, then back to the same dossier.
  await page.locator('a[href^="/clusters/?id="]').first().click()
  await expect(page).toHaveURL(/\/clusters\/\?id=/)
  await page.goBack()
  await expect(page.getByRole('heading', { name: 'Articles', exact: true })).toBeVisible()

  // Article, then the labelled way back to the event.
  await page.locator('a[href^="/articles/?article="]').first().click()
  await expect(page).toHaveURL(/\/articles\/\?/)
  await page.getByRole('link', { name: 'Back to event' }).click()
  await expect(page).toHaveURL(/\/events\/detail\/\?id=/)

  // Entity dossier, and from there the events that entity characterises.
  await page.locator('a[href^="/entities/?id="]').first().click()
  await expect(page).toHaveURL(/\/entities\/\?id=/)
  await page.getByRole('link', { name: 'Events with this entity' }).click()
  await expect(page).toHaveURL(/\/events\/\?entity_id=/)
  await expect(page.locator('a[href^="/events/detail/"]').first()).toBeVisible()
})
