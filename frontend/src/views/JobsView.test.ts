import { fireEvent, render, screen } from '@testing-library/vue'
import { VueQueryPlugin } from '@tanstack/vue-query'
import { beforeEach, expect, it, vi } from 'vitest'

import { api } from '../api'
import JobsView from './JobsView.vue'

vi.mock('../api', () => ({
  api: { jobs: vi.fn(), backlog: vi.fn(), retryJob: vi.fn() },
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
  vi.mocked(api.jobs).mockResolvedValue({ items: [failedJob], next_cursor: null })
  vi.mocked(api.backlog).mockResolvedValue({ queued: 0, running: 0, retrying: 0, failed: 1 })
})

it('shows retry progress and prevents duplicate retry requests', async () => {
  vi.mocked(api.retryJob).mockReturnValue(new Promise(() => {}))
  render(JobsView, { global: { plugins: [VueQueryPlugin] } })
  const retry = await screen.findByRole('button', { name: 'Retry' })

  await fireEvent.click(retry)

  expect(retry.hasAttribute('disabled')).toBe(true)
  expect(screen.getByText('Retrying job…')).toBeTruthy()
})

it('shows a retry failure without hiding the failed job', async () => {
  vi.mocked(api.retryJob).mockRejectedValue(new Error('failed'))
  render(JobsView, { global: { plugins: [VueQueryPlugin] } })
  await fireEvent.click(await screen.findByRole('button', { name: 'Retry' }))

  expect(await screen.findByText('Could not retry the job.')).toBeTruthy()
  expect(screen.getAllByText('Broken article').length).toBeGreaterThan(0)
})
