import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it } from 'vitest'
import { navigationHarness, resetNavigationHarness } from '../test/navigation-harness'
import { QuickSearch } from './QuickSearch'
import { pageTitle } from './Shell'

beforeEach(() => resetNavigationHarness({ pathname: '/sources/' }))
afterEach(cleanup)

it('opens Search with the typed query', () => {
  render(<QuickSearch />)
  const box = screen.getByRole('searchbox', { name: /Quick search/ })
  fireEvent.change(box, { target: { value: '  harbor strike ' } })
  fireEvent.submit(box)

  expect(navigationHarness.pathname).toBe('/search/')
  expect(navigationHarness.searchParams.get('q')).toBe('harbor strike')
  expect((box as HTMLInputElement).value).toBe('')
})

it('ignores an empty query', () => {
  render(<QuickSearch />)
  fireEvent.submit(screen.getByRole('searchbox', { name: /Quick search/ }))
  expect(navigationHarness.pathname).toBe('/sources/')
})

it('focuses on "/" unless another field has focus', () => {
  render(<><QuickSearch /><input aria-label="Other field" /></>)
  const box = screen.getByRole('searchbox', { name: /Quick search/ })

  fireEvent.keyDown(document.body, { key: '/' })
  expect(document.activeElement).toBe(box)

  const other = screen.getByLabelText('Other field')
  other.focus()
  fireEvent.keyDown(other, { key: '/' })
  expect(document.activeElement).toBe(other)
})

it('titles each tab after its page', () => {
  expect(pageTitle('/', true)).toBe('NewsIntel')
  expect(pageTitle('/search/', true)).toBe('Search · NewsIntel')
  expect(pageTitle('/monitors/', true)).toBe('Watchlist · NewsIntel')
  expect(pageTitle('/sources/detail/', true)).toBe('Source · NewsIntel')
  expect(pageTitle('/search/', false)).toBe('Sign in · NewsIntel')
})
