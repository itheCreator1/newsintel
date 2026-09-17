import { cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '../../lib/api'
import { resetNavigationHarness } from '../../test/navigation-harness'
import { renderWithQuery } from '../../test/render'
import SettingsPage from './page'

vi.mock('../../lib/api', () => ({ api: { stopWords: vi.fn(), updateStopWords: vi.fn(), nlpStatus: vi.fn() } }))

beforeEach(() => {
  vi.clearAllMocks()
  resetNavigationHarness()
  vi.mocked(api.stopWords).mockResolvedValue({ language: 'en', revision: 4, words: ['and', 'the'], configuration_fingerprint: 'fingerprint' })
  vi.mocked(api.nlpStatus).mockResolvedValue({ queued: 0, running: 0, retrying: 0, failed: 0, reprocessing: [], capabilities: [{ name: 'entities', state: 'available', version: '3.8.0', detail: null }] })
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
