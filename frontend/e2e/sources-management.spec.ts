import { readFile } from 'node:fs/promises'
import { expect, test, type Page } from '@playwright/test'

async function login(page: Page) {
  await page.goto('/')
  await page.getByLabel('Username').fill('phase17a')
  await page.getByLabel('Password').fill('phase17a-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('link', { name: 'Saved Searches' })).toBeVisible()
}

const OPML = `<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0"><head><title>Reader export</title></head><body>
  <outline text="Greek"><outline type="rss" text="Greek Wire again" xmlUrl="http://fixture/greek-wire.xml"/></outline>
  <outline type="rss" text="OPML only" xmlUrl="http://fixture/opml-only.xml"/>
</body></opml>`

// Runs after "greek entities workflow", which adds Greek Wire: the feed every step here finds again.
// It retires the one source it adds, so nothing after it sees a new source.
test('sources management workflow tests, edits, imports and exports sources', async ({ page }) => {
  await login(page)
  await page.getByRole('link', { name: 'Sources', exact: true }).click()
  const add = page.getByRole('form', { name: 'Add source' })

  // A site address leads to the feed its page announces.
  await add.getByLabel('Feed URL').fill('http://fixture/site.html')
  await add.getByRole('button', { name: 'Test feed' }).click()
  const found = page.getByRole('status', { name: 'Feed test' })
  await expect(found.getByText(/^Greek Wire · \d+ entr(y|ies) · Greek \(el\)$/)).toBeVisible({ timeout: 30_000 })
  await expect(found.getByText('Found the feed linked from http://fixture/site.html')).toBeVisible()
  await expect(found.getByText('This feed is already a source.')).toBeVisible()

  // Tags given in the edit form filter the list.
  await page.getByLabel('Find a source').fill('Greek Wire')
  await page.getByRole('button', { name: 'Edit Greek Wire' }).click()
  const edit = page.getByRole('form', { name: 'Edit Greek Wire' })
  await edit.getByLabel('Tags').fill('greek, wire')
  await edit.getByRole('button', { name: 'Save changes' }).click()
  await expect(edit).toBeHidden()
  await page.getByLabel('Find a source').fill('')
  await page.getByRole('button', { name: 'Filter by tag greek' }).click()
  await expect(page.getByLabel('Tag')).toHaveValue('greek')
  await expect(page.locator('.source-row')).toHaveCount(1)
  await expect(page.locator('.source-row').getByText('Greek Wire', { exact: true })).toBeVisible()
  await page.getByLabel('Tag').selectOption('')

  // An OPML file adds only what is new.
  await page.getByLabel('Import OPML').setInputFiles({ name: 'reader.opml', mimeType: 'text/x-opml', buffer: Buffer.from(OPML) })
  const preview = page.getByRole('region', { name: 'OPML preview' })
  await expect(preview.getByText('Already a source')).toBeVisible()
  await expect(preview.getByRole('checkbox', { name: 'OPML only' })).toBeChecked()
  await preview.getByRole('button', { name: 'Add 1 source' }).click()
  await expect(page.getByText('Added 1 source.')).toBeVisible()
  await page.getByLabel('Find a source').fill('OPML only')
  await page.getByRole('button', { name: 'Edit OPML only' }).click()
  page.once('dialog', dialog => dialog.accept())
  await page.getByRole('form', { name: 'Edit OPML only' }).getByRole('button', { name: 'Retire source' }).click()
  await expect(page.getByText('No sources match these filters.')).toBeVisible()
  await page.getByLabel('Find a source').fill('')

  // The export carries the tags back out.
  const download = page.waitForEvent('download')
  await page.getByRole('link', { name: 'Export OPML' }).click()
  const opml = await readFile(await (await download).path(), 'utf8')
  expect(opml).toContain('xmlUrl="http://fixture/greek-wire.xml"')
  expect(opml).toContain('category="/greek,/wire"')
  expect(opml).not.toContain('opml-only.xml')
})
