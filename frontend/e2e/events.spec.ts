import { expect, test, type Page } from '@playwright/test'

async function login(page: Page) {
  await page.goto('/')
  await page.getByLabel('Username').fill('phase12d')
  await page.getByLabel('Password').fill('phase12d-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: /^Investigations/ })).toBeVisible()
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
  // The fixture's events hold one story each, which the list hides until asked.
  await expect(page.getByText('No events with two or more stories match these filters.')).toBeVisible()
  await page.getByRole('checkbox', { name: 'Show one-story events', exact: true }).click()
  await expect(page).toHaveURL(/all=1/)
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
  await expect(page.getByText(/^Started this event/).first()).toBeVisible()

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
  await page.getByRole('checkbox', { name: 'Show one-story events', exact: true }).click()
  await expect(page.locator('a[href^="/events/detail/"]').first()).toBeVisible()
})

test('an event is found by a word in its headline, searched within, and watched', async ({ page }) => {
  await login(page)
  const firstHeadline = () => page.evaluate(async () => {
    const body = await (await fetch('/api/v1/events?min_stories=1')).json()
    return (body.items as { headline: string | null }[] | undefined)?.find(item => item.headline)?.headline ?? ''
  })
  await expect.poll(firstHeadline, { timeout: 120_000, intervals: [2_000] }).not.toBe('')
  const headline = await firstHeadline()
  const word = headline.split(/\W+/).find(part => part.length >= 4) ?? headline

  await page.goto('/events/?all=1')
  await page.getByRole('searchbox', { name: 'Search events' }).fill(word)
  await page.getByRole('search', { name: 'Search events' }).getByRole('button', { name: 'Search' }).click()
  await expect.poll(() => new URL(page.url()).searchParams.get('q')).toBe(word)
  await page.getByRole('link', { name: headline, exact: true }).first().click()
  await expect(page).toHaveURL(/\/events\/detail\/\?id=/)

  await page.getByRole('link', { name: 'Search within this event' }).click()
  await expect(page).toHaveURL(/\/search\/\?event_id=/)
  await expect(page.getByRole('button', { name: `Remove Event: ${headline}` })).toBeVisible()
  // The event's headline is one of its stories' representative articles, so it is among the results.
  await expect(page.locator('article.search-result').getByText(headline, { exact: true }).first()).toBeVisible({ timeout: 30_000 })

  await page.goBack()
  await page.getByRole('button', { name: 'Watch', exact: true }).click()
  await page.getByLabel('Monitor name').fill(`Event watch ${Date.now()}`)
  await page.getByRole('button', { name: 'Watch event' }).click()
  await expect(page.getByText(/Watching “Event watch/)).toBeVisible()
})
