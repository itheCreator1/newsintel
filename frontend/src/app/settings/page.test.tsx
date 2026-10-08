import { cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '../../lib/api'
import { resetNavigationHarness } from '../../test/navigation-harness'
import { renderWithQuery } from '../../test/render'
import SettingsPage from './page'

vi.mock('../../lib/api', async importOriginal => ({ ...(await importOriginal<typeof import('../../lib/api')>()), api: { stopWords: vi.fn(), updateStopWords: vi.fn(), nlpStatus: vi.fn(), greekEntities: vi.fn(), updateGreekEntities: vi.fn() } }))

beforeEach(() => {
  vi.clearAllMocks()
  resetNavigationHarness()
  vi.mocked(api.stopWords).mockResolvedValue({ language: 'en', revision: 4, words: ['and', 'the'], configuration_fingerprint: 'fingerprint' })
  vi.mocked(api.nlpStatus).mockResolvedValue({ queued: 0, running: 0, retrying: 0, failed: 0, reprocessing: [], capabilities: [{ name: 'entities', state: 'available', version: '3.8.0', detail: null }] })
  vi.mocked(api.greekEntities).mockResolvedValue({ enabled: false, available: true, detail: null, greek_article_count: 12, reprocessing_run_id: null, queued_article_count: 0 })
})
afterEach(cleanup)

it('updates stop words with revision protection and explains archive reprocessing', async () => {
  vi.mocked(api.updateStopWords).mockResolvedValue({ language: 'en', revision: 5, words: ['and', 'policy'], configuration_fingerprint: 'new' })
  renderWithQuery(() => <SettingsPage />)
  expect(await screen.findByText(/revision 4/)).toBeTruthy()
  expect(screen.getByText(/Existing annotations require reprocessing/)).toBeTruthy()
  expect(screen.getByText((_, node) => node?.textContent === 'entities · available · 3.8.0')).toBeTruthy()
  fireEvent.change(screen.getByLabelText('English stop words'), { target: { value: 'and\npolicy' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Save stop words' }))
  expect(api.updateStopWords).toHaveBeenCalledWith(4, ['and', 'policy'])
  expect(await screen.findByText('Stop words saved as revision 5.')).toBeTruthy()
})

it('turns Greek entities on and queues the Greek archive', async () => {
  vi.mocked(api.updateGreekEntities).mockResolvedValue({ enabled: true, available: true, detail: null, greek_article_count: 12, reprocessing_run_id: '00000000-0000-0000-0000-000000000001', queued_article_count: 12 })
  renderWithQuery(() => <SettingsPage />)
  const toggle = await screen.findByRole('switch')
  expect(screen.getByLabelText('Also process the 12 Greek articles already in the archive')).toBeTruthy()
  await fireEvent.click(toggle)
  expect(api.updateGreekEntities).toHaveBeenCalledWith(true, true)
  expect(await screen.findByText('Greek entities are on. 12 Greek articles will be processed in the background.')).toBeTruthy()
  expect((screen.getByRole('switch') as HTMLInputElement).checked).toBe(true)
})

it('keeps the Greek switch off and explains why when the model is missing', async () => {
  vi.mocked(api.greekEntities).mockResolvedValue({ enabled: false, available: false, detail: 'Entity recognition is off: start the app with docker/compose.ner.yaml', greek_article_count: 3, reprocessing_run_id: null, queued_article_count: 0 })
  renderWithQuery(() => <SettingsPage />)
  expect(((await screen.findByRole('switch')) as HTMLInputElement).disabled).toBe(true)
  expect(screen.getByText('Not available: Entity recognition is off: start the app with docker/compose.ner.yaml')).toBeTruthy()
})
