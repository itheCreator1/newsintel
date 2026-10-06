import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError } from '../lib/api'
import { LoadError } from './Feedback'

afterEach(cleanup)

const query = (error: unknown, isFetching = false) => ({ error, isFetching, refetch: vi.fn() })

it('offers Retry for an outage and refetches on click', () => {
  const failed = query(new ApiError('Request failed', 503))
  render(<LoadError query={failed} message="Could not load sources." />)

  expect(screen.getByRole('alert')).toHaveTextContent('Could not load sources.')
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  expect(failed.refetch).toHaveBeenCalledOnce()
})

it('offers Retry for a network failure', () => {
  render(<LoadError query={query(new TypeError('Failed to fetch'))} message="Could not load sources." />)
  expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()
})

it('shows no Retry for an answer that will not change', () => {
  render(<LoadError query={query(new ApiError('Not found', 404))} message="Could not load this source." />)
  expect(screen.queryByRole('button')).toBeNull()
})

it('disables Retry while the refetch runs', () => {
  render(<LoadError query={query(new ApiError('Request failed', 503), true)} message="Could not load sources." />)
  expect(screen.getByRole('button', { name: 'Retrying…' })).toBeDisabled()
})
