import { cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '../../lib/api'
import { resetNavigationHarness } from '../../test/navigation-harness'
import { renderWithQuery } from '../../test/render'
import JobsPage from './page'

vi.mock('../../lib/api', () => ({
  api: { jobs: vi.fn(), backlog: vi.fn(), retryJob: vi.fn(), indexingStatus: vi.fn(), indexingFailures: vi.fn(), retryIndexing: vi.fn(), nlpStatus: vi.fn(), nlpFailures: vi.fn(), retryNlpJob: vi.fn() },
}))

const failedJob = {
  id: 'job-one', article_id: 'article-one', article_title: 'Broken article',
  requested_mode: 'full_text', stage: 'fetch', status: 'failed',
  error_category: 'network', error_message: 'connection failed',
  created_at: '2026-09-13T12:00:00Z', started_at: null, completed_at: null,
  next_attempt_at: '2026-09-13T12:00:00Z', attempts: [],
}

beforeEach(() => {
  vi.clearAllMocks()
  resetNavigationHarness()
  vi.mocked(api.jobs).mockResolvedValue({ items: [failedJob], next_cursor: null })
  vi.mocked(api.backlog).mockResolvedValue({ queued: 0, running: 0, retrying: 0, failed: 1 })
  vi.mocked(api.indexingStatus).mockResolvedValue({ queued: 2, running: 0, retrying: 1, failed: 1, active_rebuild: null })
  vi.mocked(api.indexingFailures).mockResolvedValue({ items: [{ id: 'failure-one', article_id: 'article-one', index_name: 'articles-v1', attempt_count: 3, error_category: 'document', error_message: 'too large', updated_at: '2026-09-14T12:00:00Z' }], next_cursor: null })
  vi.mocked(api.nlpStatus).mockResolvedValue({ queued: 3, running: 1, retrying: 2, failed: 1, capabilities: [{ name: 'entities', state: 'disabled', detail: 'NER is disabled', version: null }], reprocessing: [{ id: 'run-one', status: 'running', scanned: 100 }] })
  vi.mocked(api.nlpFailures).mockResolvedValue({ items: [{ id: 'nlp-failure', article_id: 'article-one', processor: 'keywords', attempt_count: 5, error_category: 'input_too_large', error_message: 'Input exceeds limit', created_at: '2026-09-14T12:00:00Z' }], next_cursor: null })
})
afterEach(cleanup)

it('shows retry progress and prevents duplicate retry requests', async () => {
  vi.mocked(api.retryJob).mockReturnValue(new Promise(() => {}))
  renderWithQuery(() => <JobsPage />)
  const retry = await screen.findByRole('button', { name: 'Retry' })

  await fireEvent.click(retry)

  await vi.waitFor(() => expect(retry.hasAttribute('disabled')).toBe(true))
  expect(screen.getByText('Retrying job…')).toBeTruthy()
})

it('shows a retry failure without hiding the failed job', async () => {
  vi.mocked(api.retryJob).mockRejectedValue(new Error('failed'))
  renderWithQuery(() => <JobsPage />)
  await fireEvent.click(await screen.findByRole('button', { name: 'Retry' }))

  expect(await screen.findByText('Could not retry the job.')).toBeTruthy()
  expect(screen.getAllByText('Broken article').length).toBeGreaterThan(0)
})

it('resets pagination when a filter changes', async () => {
  vi.mocked(api.jobs)
    .mockResolvedValueOnce({ items: [failedJob], next_cursor: 'next-page' })
    .mockResolvedValue({ items: [failedJob], next_cursor: null })
  renderWithQuery(() => <JobsPage />)
  await fireEvent.click(await screen.findByRole('button', { name: 'Next page' }))
  await vi.waitFor(() => expect(api.jobs).toHaveBeenLastCalledWith(expect.objectContaining({ cursor: 'next-page' })))
  fireEvent.change(screen.getByRole('combobox', { name: 'Stage' }), { target: { value: 'fetch' } })

  await vi.waitFor(() => expect(api.jobs).toHaveBeenLastCalledWith({ stage: 'fetch', status: undefined, cursor: undefined }))
})

it('shows backlog loading and errors', async () => {
  vi.mocked(api.backlog).mockReturnValue(new Promise(() => {}))
  const rendered = renderWithQuery(() => <JobsPage />)
  expect(screen.getByText('Loading backlog…')).toBeTruthy()
  rendered.unmount()
  vi.mocked(api.backlog).mockRejectedValue(new Error('offline'))
  renderWithQuery(() => <JobsPage />)
  expect(await screen.findByText('Could not load backlog.')).toBeTruthy()
})

it('shows successful retry feedback', async () => {
  vi.mocked(api.retryJob).mockResolvedValue({ job_id: 'new-job', status: 'queued', reused: false })
  renderWithQuery(() => <JobsPage />)
  await fireEvent.click(await screen.findByRole('button', { name: 'Retry' }))

  expect(await screen.findByText('Retry scheduled.')).toBeTruthy()
})

it('shows indexing backlog and retries a failed article', async () => {
  vi.mocked(api.retryIndexing).mockResolvedValue({ status: 'queued' })
  renderWithQuery(() => <JobsPage />)
  expect(await screen.findByText('Search indexing')).toBeTruthy()
  await fireEvent.click(await screen.findByRole('button', { name: 'Retry indexing' }))
  expect(api.retryIndexing).toHaveBeenCalledWith('article-one')
  expect(await screen.findByText('Indexing retry scheduled.')).toBeTruthy()
})

it('shows NLP backlog, capabilities, reprocessing progress, and retries failures', async () => {
  vi.mocked(api.retryNlpJob).mockResolvedValue({ status: 'queued', jobs_created: 1 })
  renderWithQuery(() => <JobsPage />)
  expect(await screen.findByText('NLP processing')).toBeTruthy()
  expect(await screen.findByText((_, node) => node?.textContent === 'entities · disabled · NER is disabled')).toBeTruthy()
  expect(screen.getByText(/100 articles scanned/)).toBeTruthy()
  await fireEvent.click(screen.getByRole('button', { name: 'Retry NLP' }))
  expect(api.retryNlpJob).toHaveBeenCalledWith('nlp-failure')
  expect(await screen.findByText('NLP retry scheduled.')).toBeTruthy()
})

it('links to the operational metrics', async () => {
  renderWithQuery(() => <JobsPage />)
  expect((await screen.findByRole('link', { name: 'Operational metrics' })).getAttribute('href')).toBe('/operations/')
})

it('marks a failed job as an error, not as pending', async () => {
  const { container } = renderWithQuery(() => <JobsPage />)
  await screen.findByText(failedJob.article_title)
  const badge = [...container.querySelectorAll('.job-row span')].find(el => el.textContent === 'failed')
  expect(badge?.className).toContain('text-destructive')
})
