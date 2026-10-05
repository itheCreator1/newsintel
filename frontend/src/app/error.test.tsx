import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import RouteError from './error'

afterEach(cleanup)

it('replaces a crashed page with a notice that can retry it', () => {
  const reset = vi.fn()
  render(<RouteError error={new Error('boom')} reset={reset} />)

  expect(screen.getByRole('alert').textContent).toBe('This page failed to render.')
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  expect(reset).toHaveBeenCalledOnce()
})
