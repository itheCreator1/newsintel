import { cleanup, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../lib/api'
import { renderWithQuery } from '../test/render'
import { EntityExtractionNotice, GettingStarted } from './GettingStarted'

vi.mock('../lib/api', async importOriginal => ({ ...(await importOriginal<typeof import('../lib/api')>()), api: { feeds: vi.fn(), articles: vi.fn(), indexingStatus: vi.fn(), nlpStatus: vi.fn() } }))

const indexStatus = (ready: boolean) => ({ queued: 0, running: 0, retrying: 0, failed: 0, active_rebuild: null, index_ready: ready })
const nlpStatus = (state: string, detail: string | null = null) => ({ queued: 0, running: 0, retrying: 0, failed: 0, capabilities: [{ name: 'entities', state, detail, version: null }], reprocessing: [] })

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.feeds).mockResolvedValue({ items: [], next_cursor: null })
  vi.mocked(api.articles).mockResolvedValue({ items: [], next_cursor: null })
  vi.mocked(api.indexingStatus).mockResolvedValue(indexStatus(false))
})
afterEach(cleanup)

it('lists every first-run step on a fresh install', async () => {
  renderWithQuery(() => <GettingStarted />)

  expect(await screen.findByRole('heading', { name: 'Getting started' })).toBeTruthy()
  expect(screen.getByText('3 steps left', { exact: false })).toBeTruthy()
  expect(screen.getByRole('link', { name: 'Add a feed' }).getAttribute('href')).toBe('/sources/')
  expect(screen.getByText('python -m app.cli rebuild-search')).toBeTruthy()
})

it('marks finished steps done and keeps the rest', async () => {
  vi.mocked(api.feeds).mockResolvedValue({ items: [{} as never], next_cursor: null })
  vi.mocked(api.indexingStatus).mockResolvedValue(indexStatus(true))
  renderWithQuery(() => <GettingStarted />)

  expect(await screen.findByText('One step left', { exact: false })).toBeTruthy()
  expect(screen.getByText('Add a source', { exact: false }).textContent).toBe('Add a source (done)')
  expect(screen.getByText('Collect the first articles', { exact: false }).textContent).toBe('Collect the first articles (to do)')
  expect(screen.queryByRole('link', { name: 'Add a feed' })).toBeNull()
  expect(screen.getByRole('link', { name: 'View the collection' })).toBeTruthy()
})

it('disappears once the archive is ready', async () => {
  vi.mocked(api.feeds).mockResolvedValue({ items: [{} as never], next_cursor: null })
  vi.mocked(api.articles).mockResolvedValue({ items: [{} as never], next_cursor: null })
  vi.mocked(api.indexingStatus).mockResolvedValue(indexStatus(true))
  const { container } = renderWithQuery(() => <GettingStarted />)

  await vi.waitFor(() => expect(api.indexingStatus).toHaveBeenCalled())
  await new Promise(resolve => setTimeout(resolve, 0))
  expect(container.textContent).toBe('')
})

it('leaves out a step whose check failed instead of calling it undone', async () => {
  vi.mocked(api.feeds).mockResolvedValue({ items: [{} as never], next_cursor: null })
  vi.mocked(api.articles).mockResolvedValue({ items: [{} as never], next_cursor: null })
  vi.mocked(api.indexingStatus).mockRejectedValue(new ApiError('Request failed', 500))
  const { container } = renderWithQuery(() => <GettingStarted />)

  await vi.waitFor(() => expect(api.indexingStatus).toHaveBeenCalled())
  await new Promise(resolve => setTimeout(resolve, 0))
  expect(container.textContent).toBe('')
})

it('explains that entity extraction is off', async () => {
  vi.mocked(api.nlpStatus).mockResolvedValue(nlpStatus('disabled'))
  renderWithQuery(() => <EntityExtractionNotice />)

  expect(await screen.findByRole('heading', { name: 'Entity extraction is off' })).toBeTruthy()
  expect(screen.getByText('docker/compose.ner.yaml')).toBeTruthy()
})

it('passes on the configuration problem when entity extraction is broken', async () => {
  vi.mocked(api.nlpStatus).mockResolvedValue(nlpStatus('configuration_failure', "spaCy model 'en_core_web_lg' is not installed"))
  renderWithQuery(() => <EntityExtractionNotice />)

  expect(await screen.findByRole('heading', { name: 'Entity extraction is not working' })).toBeTruthy()
  expect(screen.getByText("spaCy model 'en_core_web_lg' is not installed", { exact: false })).toBeTruthy()
})

it('says nothing when entity extraction is available', async () => {
  vi.mocked(api.nlpStatus).mockResolvedValue(nlpStatus('available'))
  const { container } = renderWithQuery(() => <EntityExtractionNotice />)

  await vi.waitFor(() => expect(api.nlpStatus).toHaveBeenCalled())
  await new Promise(resolve => setTimeout(resolve, 0))
  expect(container.textContent).toBe('')
})
