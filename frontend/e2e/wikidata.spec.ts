import { expect, test, type Page } from '@playwright/test'

async function login(page: Page, user = 'phase15a') {
  await page.goto('/')
  await page.getByLabel('Username').fill(user)
  await page.getByLabel('Password').fill(`${user}-password`)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('link', { name: 'Saved Searches' })).toBeVisible()
}

// The worker asks the fixture server's stand-in for api.php (docker/compose.e2e.yaml), never
// Wikidata. infra/test-e2e.sh seeds "Ada Lindqvist", a name no fixture article has; the stand-in
// knows two items of that name: a diplomat (a human, Q900001) and a film (Q900002).
test('wikidata workflow suggests, links and fetches an item', async ({ page }) => {
  test.setTimeout(240_000)
  await login(page)
  await page.goto('/authorities')
  await page.getByLabel('Find a name').fill('lindqvist')
  const file = page.getByRole('list', { name: 'Authority file' })
  await expect(file.getByRole('listitem')).toHaveCount(1)
  await file.getByRole('link', { name: 'Ada Lindqvist' }).click()
  await expect(page.getByRole('heading', { level: 3, name: 'Ada Lindqvist' })).toBeVisible()

  // Nothing is asked of Wikidata until the button: then the run waits its turn, one request at a time.
  await expect(page.getByText('No Wikidata suggestions yet.')).toBeVisible()
  await page.getByRole('button', { name: 'Find on Wikidata' }).click()
  const suggestions = page.getByRole('list', { name: 'Wikidata suggestions' })
  await expect(suggestions.getByRole('listitem')).toHaveCount(2, { timeout: 120_000 })
  const diplomat = suggestions.getByRole('listitem').first()
  await expect(diplomat.getByRole('link', { name: 'Q900001' })).toHaveAttribute('href', 'https://www.wikidata.org/wiki/Q900001')
  await expect(diplomat).toContainText('Swedish diplomat (invented for the tests)')
  await expect(diplomat).toContainText('Exact label (en)')
  await expect(diplomat).toContainText('Type matches')
  await expect(suggestions.getByRole('listitem').nth(1)).toContainText('Type differs')
  await expect(page.getByText('Data from Wikidata (CC0)')).toBeVisible()

  await page.getByRole('button', { name: 'Not this one: Q900002' }).click()
  await expect(suggestions.getByRole('listitem')).toHaveCount(1)
  await page.getByRole('button', { name: 'Link to Q900001' }).click()
  await expect(page.getByText('Linked to Q900001')).toBeVisible()

  // The labels are added at once; the full fetch brings the identifiers and the aliases to offer.
  await expect(page.getByRole('list', { name: 'Other names' })).toContainText('Άντα Λίντκβιστ')
  const ids = page.getByRole('list', { name: 'Identifiers' })
  await expect(ids.getByRole('link', { name: 'VIAF 900001' })).toHaveAttribute('href', 'https://viaf.org/viaf/900001', { timeout: 120_000 })
  await expect(ids.getByRole('link', { name: 'ISNI 0000 0000 9000 0001' })).toHaveAttribute('href', 'https://isni.org/isni/0000000090000001')
  await page.getByRole('checkbox', { name: 'A. Lindqvist (en, alias)' }).check()
  await page.getByRole('button', { name: 'Add selected names' }).click()
  await expect(page.getByRole('list', { name: 'Other names' })).toContainText('A. Lindqvist')
  await expect(page.getByRole('list', { name: 'Authority history' })).toContainText('Linked to Wikidata Q900001')

  // The Authority file shows the QID and filters by it.
  await page.goto('/authorities')
  await page.getByRole('combobox', { name: /^Wikidata/ }).selectOption('linked')
  await expect(file.getByRole('listitem')).toHaveCount(1)
  await expect(file.getByRole('link', { name: 'Q900001' })).toBeVisible()
  await expect(file.getByRole('link', { name: 'Ada Lindqvist' })).toBeVisible()

  // Processes shows what was asked, within the budget, and the one link.
  await page.goto('/processes')
  const card = page.getByRole('region', { name: 'Wikidata' })
  await expect(card.getByText('Open', { exact: true })).toBeVisible()
  await expect(card.getByText('Linked entities').locator('xpath=following-sibling::dd')).toHaveText('1')
  await expect(card.getByText('Requests today').locator('xpath=following-sibling::dd')).toHaveText(/^[1-9]\d* of 2000$/)
})
