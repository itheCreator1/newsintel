import { expect, test, type Page } from '@playwright/test'

// Runs after `relationships seed` (a Greek and a US feed, indexed), like the map spec.
async function login(page: Page) {
  await page.goto('/')
  await page.getByLabel('Username').fill('phase13d')
  await page.getByLabel('Password').fill('phase13d-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
}

async function expectNoHorizontalOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
}

test('ui polish workflow', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 })
  await login(page)

  // Narrow screens: the navigation is closed, out of the tab order, and keyboard operable.
  const menu = page.getByRole('button', { name: 'Menu' })
  await expect(menu).toHaveAttribute('aria-expanded', 'false')
  await expect(page.getByRole('link', { name: 'Search', exact: true })).toBeHidden()
  await menu.focus()
  await page.keyboard.press('Enter')
  await expect(menu).toHaveAttribute('aria-expanded', 'true')
  await page.keyboard.press('Tab')
  await page.keyboard.press('Escape')
  await expect(menu).toHaveAttribute('aria-expanded', 'false')
  await expect(menu).toBeFocused()
  await menu.click()
  await page.getByRole('link', { name: 'Search', exact: true }).click()
  await expect(page).toHaveURL(/\/search\/$/)
  await expect(menu).toHaveAttribute('aria-expanded', 'false')
  await expectNoHorizontalOverflow(page)

  // Advanced filters starts closed, keeps a draft while collapsed, and reopens for applied criteria.
  const advanced = page.locator('summary').filter({ hasText: 'Advanced filters' })
  const storyCountry = page.getByLabel('Story country', { exact: true })
  await expect(storyCountry).toBeHidden()
  await advanced.click()
  await page.getByLabel('Source country', { exact: true }).fill('ZZ')
  await advanced.click()
  await expect(storyCountry).toBeHidden()
  await page.getByRole('button', { name: 'Search archive' }).click()
  await expect(page).toHaveURL(/country=ZZ/)
  await expect(advanced).toHaveText('Advanced filters (1)')
  await expect(storyCountry).toBeVisible()
  await expect(page.getByText('No articles match this search.')).toBeVisible({ timeout: 30_000 })

  // A chip removes its criterion and brings the results back; Back restores it.
  await page.getByRole('button', { name: 'Remove Source country: ZZ' }).click()
  await expect(page).not.toHaveURL(/country=/)
  await expect(page.locator('.search-result').first()).toBeVisible({ timeout: 30_000 })
  await page.goBack()
  await expect(page.getByRole('button', { name: 'Remove Source country: ZZ' })).toBeVisible()
  await page.getByRole('button', { name: 'Clear all filters' }).first().click()
  await expect(page).toHaveURL(/\/search\/$/)

  // Graph: clear-all keeps the node count.
  await page.goto('/graph/?country=GR&nodes=20')
  await expect(page.getByRole('button', { name: 'Remove Source country: GR' })).toBeVisible()
  await page.getByRole('button', { name: 'Clear all filters' }).first().click()
  await expect(page).toHaveURL(/\/graph\/\?nodes=20$/)

  // Every width: no horizontal overflow on the touched pages.
  for (const width of [360, 768, 1280]) {
    await page.setViewportSize({ width, height: 900 })
    for (const path of ['/', '/search/?q=Relationships', '/graph/']) {
      await page.goto(path)
      await expect(page.getByRole('main')).toBeVisible()
      await expectNoHorizontalOverflow(page)
    }
  }

  // Reduced motion stills the status ping.
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/')
  expect(await page.locator('.animate-ping').evaluate(node => getComputedStyle(node).animationIterationCount)).toBe('1')

  // Wide screens: the sidebar shows every group, marks the current page, and the skip link reaches main.
  await page.goto('/graph/')
  await expect(menu).toBeHidden()
  await expect(page.getByRole('link', { name: 'Graph', exact: true })).toHaveAttribute('aria-current', 'page')
  await page.keyboard.press('Tab')
  const skip = page.getByRole('link', { name: 'Skip to content' })
  await expect(skip).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('main')).toBeFocused()
})
